# Agent Skills

QAagent v1 supports deployment-managed, single-file instruction Skills. A Skill is not a plugin: it cannot install dependencies, execute bundled scripts, register MCP servers or read sibling assets/references. User personalization remains separate database-backed, untrusted preference data.

Each configured `SKILL_ROOTS` directory may contain `*/SKILL.md` files with bounded scalar frontmatter:

```markdown
---
name: grounded-knowledge
description: Answer document questions using only authorized retrieved evidence.
version: 1
---
Call `knowledge_search`, use only authorized evidence and state when evidence is insufficient.
```

Names are lowercase ASCII slugs. Descriptions are single-line catalog text. Startup validates metadata, total bytes, body-token estimate, root containment, duplicate names and catalog-token size. Every file and the complete catalog receive SHA-256 digests. Invalid optional Skills are skipped with content-free logs; duplicate names, an oversized catalog or a missing `REQUIRED_SKILLS` entry fail startup/readiness.

The model sees only the validated name/description catalog. `activate_skill` returns a content-free `name + version + digest` receipt. On the following Agent node, the server verifies that receipt against the current catalog and adds only the matching body to a dedicated trusted system section. The body is never returned as ordinary tool data and is excluded from checkpoint messages, SSE summaries and long-term-memory jobs.

Skill activation uses a separate control budget. It can therefore be followed by a read-only tool call such as `knowledge_search`; duplicate and total execution guards still terminate loops. When the catalog is empty, `activate_skill` is not bound to the model.

Production images bake reviewed Skills into the immutable image. Development Compose mounts `./skills` read-only. Adding or changing a Skill requires rebuilding/restarting every worker; mixed worker versions are detected through activation and catalog digests. Never store credentials, personal data or private document content in a Skill.

Relevant settings are documented in [Configuration](configuration.md). `/ready` reports only catalog count, digest prefix and missing required names; it never exposes Skill bodies or filesystem paths.
