//! EuLLM Agent: autonomous agent runtime with multi-provider LLM support.
//!
//! The binary in `main.rs` is a thin CLI over this library; integration
//! tests in `tests/` use the same modules.

pub mod agent;
pub mod audit;
pub mod config;
pub mod llm;
pub mod modules;
pub mod setup;
pub mod telegram;
pub mod tools;
pub mod util;
pub mod wizard;
