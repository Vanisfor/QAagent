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

## Personal knowledge operations

Authenticated users can create private spaces under `/api/v1/users/me/knowledge-spaces`, upload UTF-8 `.md`, `.txt` or `.rst` files (5 MiB per file by default), and poll `/api/v1/users/me/knowledge-ingestion-jobs/{job_id}`. The backend derives the owner and organization from the login. The upload response means the job is queued; the document is searchable only after the job reports `completed`. A terminal failure can be retried by uploading the file again. Deleting a document requires the space owner; editors can upload and readers can only list. An explicit chat space selection that no longer has authorization returns no evidence.

Back up PostgreSQL and `KNOWLEDGE_UPLOAD_DIR` together. A lost raw upload cannot be rebuilt from the job row alone. Restore both before restarting the ingestion worker, then inspect failed jobs and re-upload those files. Lease tokens guard each worker's renewal, write transaction and completion; workers that lose a lease cannot commit a document. Deploy new migrations before enabling uploads. Rolling deployments should use one reviewed Skill catalog version at a time, and `/ready` should show the expected digest on every instance.

Skill activation, tool budget rejections, ingestion transitions and ACL denials expose content-free counters. Trace and log records must not include document text, user Prompt, Skill body, tool arguments or credentials. PostgreSQL ACL integration, embedding-provider connectivity, OpenSearch sync, multi-worker failover, backup/restore and browser behavior remain separate acceptance checks; a local or mocked result does not establish all of them.
