//! Path confinement for file tools.
//!
//! Every path a tool receives is resolved against one workspace directory and
//! refused when it could land outside it:
//!
//! * `..` components are refused outright, before touching the disk;
//! * absolute paths must lie under the workspace;
//! * the deepest existing ancestor is canonicalised, so a symlink inside the
//!   workspace that points outside it is caught;
//! * writes refuse an existing symlink as the target, and on Unix open with
//!   `O_NOFOLLOW` so a symlink swapped in afterwards is not followed either.

use anyhow::{bail, Context, Result};
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
}

impl Workspace {
    /// Create the directory if needed and canonicalise it.
    pub fn open(dir: &Path) -> Result<Self> {
        std::fs::create_dir_all(dir)
            .with_context(|| format!("cannot create workspace {}", dir.display()))?;
        let root = dir
            .canonicalize()
            .with_context(|| format!("cannot resolve workspace {}", dir.display()))?;
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Resolve `input` to a path inside the workspace, or refuse it.
    pub fn resolve(&self, input: &str) -> Result<PathBuf> {
        if input.is_empty() || input.contains('\0') {
            bail!("Access denied: invalid path");
        }
        let given = Path::new(input);
        let mut joined = self.root.clone();
        let relative = if given.is_absolute() {
            given
                .strip_prefix(&self.root)
                .map_err(|_| anyhow::anyhow!("Access denied: path outside the workspace"))?
                .to_path_buf()
        } else {
            given.to_path_buf()
        };
        for comp in relative.components() {
            match comp {
                Component::Normal(part) => joined.push(part),
                Component::CurDir => {}
                Component::ParentDir => bail!("Access denied: '..' is not allowed in paths"),
                Component::RootDir | Component::Prefix(_) => {
                    bail!("Access denied: path outside the workspace")
                }
            }
        }

        // Canonicalise the deepest part that exists; it must stay inside.
        let mut existing = joined.as_path();
        loop {
            if existing.symlink_metadata().is_ok() {
                break;
            }
            existing = existing
                .parent()
                .context("Access denied: path outside the workspace")?;
        }
        let canonical = existing
            .canonicalize()
            .context("Access denied: cannot resolve path")?;
        if !canonical.starts_with(&self.root) {
            bail!("Access denied: path outside the workspace");
        }
        // join("") would add a trailing separator and break files.
        match joined.strip_prefix(existing) {
            Ok(rest) if !rest.as_os_str().is_empty() => Ok(canonical.join(rest)),
            _ => Ok(canonical),
        }
    }

    /// Resolve a path that will be written: additionally refuses an existing
    /// symlink as the final component.
    pub fn resolve_for_write(&self, input: &str) -> Result<PathBuf> {
        // Check the unresolved final component before resolve() follows it.
        let given = Path::new(input);
        let lexical = if given.is_absolute() {
            given.to_path_buf()
        } else {
            self.root.join(given)
        };
        if let Ok(meta) = lexical.symlink_metadata() {
            if meta.file_type().is_symlink() {
                bail!("Access denied: refusing to write through a symlink");
            }
        }
        let path = self.resolve(input)?;
        if path == self.root {
            bail!("Access denied: cannot write the workspace itself");
        }
        Ok(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ws(name: &str) -> (Workspace, PathBuf) {
        let base = std::env::temp_dir().join(format!("eullm-ws-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        (Workspace::open(&base.join("ws")).unwrap(), base)
    }

    #[test]
    fn relative_paths_resolve_inside() {
        let (w, _) = ws("rel");
        let p = w.resolve("a/b.txt").unwrap();
        assert!(p.starts_with(w.root()));
    }

    #[test]
    fn dotdot_is_refused() {
        let (w, _) = ws("dotdot");
        assert!(w.resolve("new/../../outside.txt").is_err());
        assert!(w.resolve("..").is_err());
    }

    #[test]
    fn absolute_outside_is_refused_and_inside_is_accepted() {
        let (w, _) = ws("abs");
        assert!(w.resolve("/etc/passwd").is_err());
        let inside = w.root().join("x.txt");
        assert!(w.resolve(inside.to_str().unwrap()).is_ok());
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_pointing_outside_are_refused() {
        let (w, base) = ws("link");
        std::os::unix::fs::symlink(&base, w.root().join("escape")).unwrap();
        assert!(w.resolve("escape/anything").is_err());
        assert!(w.resolve_for_write("escape").is_err());
    }
}
