# Contributing to EuLLM Agent

Thanks for considering a contribution: code, docs, bug reports and ideas are
all welcome.

## Quick start

```bash
git clone https://github.com/eullm/eullm-agent.git && cd eullm-agent
cargo build
cargo clippy -- -D warnings
cargo test
```

## Pull requests

1. Branch from `main` and keep each pull request to one change.
2. Make sure `cargo clippy -- -D warnings` and `cargo test` pass.
3. Fill in the pull request template. External contributors also add the
   CLA line below; members of the eullm organisation do not.

## License

EuLLM Agent is licensed under [AGPL-3.0-or-later](LICENSE). I3K Technologies
Srl holds the copyright and also offers the software under a separate
commercial licence, so before a pull request can be merged you must agree to
the [Contributor Licence Agreement](CLA.md) by adding one line to your pull
request description:

```
I have read and agree to the Contributor Licence Agreement in CLA.md.
```

A check named **CLA** runs on every pull request from outside the eullm
organisation and fails until that line is in the description. Editing the description re-runs it; you do not need to push
again.

Agreeing only that your contribution is "licensed under AGPL-3.0-or-later" is
**not** the same thing and is not sufficient: it is the licence the project
already carries, and it leaves us without the right to include your work in a
commercially licensed build. [CLA.md](CLA.md) sets out what you grant, what
you keep (your own copyright: it is a licence, not an assignment), and why a
`Signed-off-by` line does not cover it.

**Important:** do not introduce dependencies with GPL, AGPL or other copyleft
licences without discussing it first in an issue: they would prevent the
commercial licence.
