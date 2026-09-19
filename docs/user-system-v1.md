# User System v1

QAagent now keeps the existing integer `user.id`, chat `session` and encrypted `user_llm_settings` contracts. The user system adds revocable login sessions, profile data, durable appearance settings, personalization and authenticated avatar storage.

## Authentication

`POST /api/v1/auth/register` and `POST /api/v1/auth/login` return a short-lived access token and set a host-only `HttpOnly`, `SameSite=Strict` `qa_refresh` cookie. `POST /api/v1/auth/refresh` requires the `X-QA-Auth: 1` header and rotates the refresh secret under a row lock. `POST /api/v1/auth/logout` revokes the current login row and clears the cookie. Browser production deployments must set `AUTH_COOKIE_SECURE=true` and keep the API origin in `ALLOWED_ORIGINS`.

Access tokens include a login-session ID and user ID. `get_current_user` rejects old unbound tokens, expired or revoked login rows, disabled users and mismatched owners. Conversation tokens carry the same binding; `get_current_session` checks that the session row belongs to the authenticated user before any checkpoint, rename, delete, chat or memory operation. Existing access tokens therefore require a fresh login after this migration; existing users, password hashes and chat rows remain intact.

## Account and settings API

- `GET /api/v1/users/me` returns safe identity and profile data.
- `GET/PATCH /api/v1/users/me/profile` edits display name, bio, language and timezone.
- `POST/DELETE/GET /api/v1/users/me/avatar` validates MIME, extension, decoded image format, dimensions and the 2 MB limit. Files are normalized to 512px PNGs under `USER_MEDIA_DIR`; the API serves them only after current-user authentication.
- `GET/PATCH /api/v1/users/me/settings` stores appearance and account-scoped background preferences. Existing `/api/v1/users/me/settings/llm` remains the encrypted BYOK API.
- `GET/PATCH /api/v1/users/me/personalization` stores personality, custom instructions, response style, verbosity and memory toggle.

The `a61d09e47b32` migration backfills profiles and empty settings rows for existing users. Docker mounts `./data/user-media` so media survives container recreation; back up this directory with the database.

## Agent and ownership

`build_system_prompt` appends validated profile and personalization as explicitly untrusted JSON below the base system policy. It restates that preferences cannot grant tools, expose secrets, override safety, or turn evidence into instructions. The Agent reads these preferences per request. A disabled memory preference skips the mem0 search, memory-job enqueue and worker write; existing memories are retained. User IDs and ACL context continue to come from authenticated server metadata. Knowledge spaces and documents use the existing organization/group ACL checks; no client-supplied `user_id` is accepted for those paths.

## Frontend

`AuthProvider` owns login refresh/logout state and `apiFetch` retries one expired access request after refresh. `useAccount` loads the backend profile/settings and downloads private avatars with the bearer token. `SettingsModal` contains General, Appearance, Personalization, Model/API and Account sections while reusing the existing Model and Background components in embedded mode. `UserProfileMenu` remains a Popover; settings stay Modal/Panel and core chat routes remain separate from settings routes. Core routes are `/login`, `/chat` and `/chat/:sessionId`.

## Deliberate v1 limits

Password reset, email verification, account deletion, “log out all devices”, external object storage, role administration and public profile URLs are not included. User IDs remain integers to avoid breaking existing session, knowledge and checkpoint relationships. Local browser appearance data from earlier versions is not imported automatically because it was not account-scoped; the backend is authoritative after the user saves the setting.
