//! Model Router: named models with fallback and pricing.
//!
//! `default` is the top-level `provider`; `models` adds more. Profiles pick
//! a model by name, so changing provider never touches the callers.

use anyhow::{bail, Context, Result};
use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use crate::config::{Config, Pricing};
use crate::llm::{ChatResponse, LlmClient, Message, ToolDefinition};
use crate::setup::build_client;

#[derive(Clone)]
pub struct RoutedModel {
    pub client: Arc<dyn LlmClient>,
    pub pricing: Option<Pricing>,
}

/// Client, pricing and fallback name of a configured model.
type Entry = (Arc<dyn LlmClient>, Option<Pricing>, Option<String>);

pub struct ModelRouter {
    models: HashMap<String, RoutedModel>,
}

impl ModelRouter {
    pub fn from_config(config: &Config) -> Result<Self> {
        let timeout = Duration::from_secs(config.limits.llm_timeout_seconds);
        let mut base: HashMap<String, Entry> = HashMap::new();
        base.insert(
            "default".into(),
            (build_client(&config.provider, timeout)?, None, None),
        );
        for (name, m) in &config.models {
            let client =
                build_client(&m.provider, timeout).with_context(|| format!("models.{name}"))?;
            base.insert(name.clone(), (client, m.pricing, m.fallback.clone()));
        }
        let mut models = HashMap::new();
        for name in base.keys() {
            // Follow the fallback chain, refusing cycles.
            let mut chain: Vec<Arc<dyn LlmClient>> = Vec::new();
            let mut seen = vec![name.clone()];
            let mut cur = name.clone();
            loop {
                let (client, _, fallback) = &base[&cur];
                chain.push(Arc::clone(client));
                match fallback {
                    Some(f) if seen.contains(f) => bail!("fallback cycle at model '{f}'"),
                    Some(f) => {
                        seen.push(f.clone());
                        cur = f.clone();
                    }
                    None => break,
                }
            }
            let client: Arc<dyn LlmClient> = if chain.len() == 1 {
                chain.pop().unwrap()
            } else {
                Arc::new(FallbackClient { chain })
            };
            models.insert(
                name.clone(),
                RoutedModel {
                    client,
                    pricing: base[name].1,
                },
            );
        }
        Ok(Self { models })
    }

    /// For tests: one client under a name.
    pub fn single(name: &str, client: Arc<dyn LlmClient>) -> Self {
        let mut models = HashMap::new();
        models.insert(
            name.to_string(),
            RoutedModel {
                client,
                pricing: None,
            },
        );
        Self { models }
    }

    pub fn get(&self, name: &str) -> Option<&RoutedModel> {
        self.models.get(name)
    }

    pub fn names(&self) -> Vec<&str> {
        let mut v: Vec<&str> = self.models.keys().map(String::as_str).collect();
        v.sort();
        v
    }
}

/// Tries each model in order until one answers.
pub struct FallbackClient {
    chain: Vec<Arc<dyn LlmClient>>,
}

impl FallbackClient {
    pub fn new(chain: Vec<Arc<dyn LlmClient>>) -> Self {
        assert!(!chain.is_empty());
        Self { chain }
    }
}

#[async_trait]
impl LlmClient for FallbackClient {
    async fn chat(&self, messages: &[Message], tools: &[ToolDefinition]) -> Result<ChatResponse> {
        let mut last_err = None;
        for (i, c) in self.chain.iter().enumerate() {
            match c.chat(messages, tools).await {
                Ok(r) => return Ok(r),
                Err(e) => {
                    if i + 1 < self.chain.len() {
                        tracing::warn!(
                            "{} / {} failed, trying fallback: {e}",
                            c.provider_name(),
                            c.model()
                        );
                    }
                    last_err = Some(e);
                }
            }
        }
        Err(last_err.unwrap())
    }

    fn provider_name(&self) -> &str {
        self.chain[0].provider_name()
    }

    fn model(&self) -> &str {
        self.chain[0].model()
    }
}
