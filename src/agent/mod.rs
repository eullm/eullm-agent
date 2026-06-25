use anyhow::Result;
use tracing::{debug, info, warn};

use crate::llm::{strip_think_blocks, LlmClient, Message, ToolCall};
use crate::tools::ToolRegistry;

pub struct Agent<'a> {
    llm: &'a dyn LlmClient,
    tools: &'a ToolRegistry,
    max_iterations: usize,
}

impl<'a> Agent<'a> {
    pub fn new(llm: &'a dyn LlmClient, tools: &'a ToolRegistry, max_iterations: usize) -> Self {
        Self { llm, tools, max_iterations }
    }

    pub async fn run(
        &self,
        system_prompt: &str,
        task: &str,
        progress: impl Fn(&str),
    ) -> Result<String> {
        let tool_defs = self.tools.definitions();
        let mut messages = vec![
            Message::system(system_prompt),
            Message::user(task),
        ];

        for iteration in 0..self.max_iterations {
            debug!("iteration {}", iteration + 1);

            let response = self.llm.chat(&messages, &tool_defs).await?;

            if response.tool_calls.is_empty() {
                info!("done after {} iterations", iteration + 1);
                return Ok(strip_think_blocks(&response.content));
            }

            messages.push(Message::assistant_with_tools(
                strip_think_blocks(&response.content),
                response.tool_calls.clone(),
            ));

            for tc in &response.tool_calls {
                let label = format!("tool:{}", tc.name);
                progress(&label);
                info!("{label}");

                let result = self.execute_tool(tc).await;
                let result_text = match result {
                    Ok(out) => out,
                    Err(e) => {
                        warn!("tool {} error: {e}", tc.name);
                        format!("Error: {e}")
                    }
                };

                messages.push(Message::tool_result(&tc.id, result_text));
            }
        }

        Err(anyhow::anyhow!(
            "exceeded max_iterations ({})",
            self.max_iterations
        ))
    }

    async fn execute_tool(&self, tc: &ToolCall) -> Result<String> {
        self.tools.execute(&tc.name, &tc.arguments).await
    }
}
