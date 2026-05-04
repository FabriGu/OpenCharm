# Contributing to OpenCharm

Thanks for your interest in contributing. This is a solo-developed project, so response times may vary, but all contributions are welcome.

## Reporting bugs

Open a [GitHub issue](https://github.com/FabriGu/OpenCharm/issues/new?template=bug_report.md) with:

- What you expected vs what happened
- Steps to reproduce
- Hardware info (board revision, firmware commit hash)
- Serial monitor output or relay server logs

## Submitting code

1. Fork the repo
2. Create a feature branch from `main`
3. Make your changes
4. Open a pull request against `main`

Keep PRs focused on one concern. Small PRs get reviewed faster.

## Coding conventions

**Firmware (C++/Arduino)**
- PlatformIO build system
- Pin definitions and constants in `firmware/include/config.h`
- FreeRTOS tasks for concurrent operations (audio capture on Core 1, WiFi on Core 0)

**Relay server (Python)**
- PEP 8 style
- Type hints encouraged
- FastAPI for HTTP endpoints
- Environment variables for all secrets (never hardcode)

**Documentation**
- Markdown in `docs/`
- Keep it direct. Short sentences.

## Commit messages

Use [conventional commits](https://www.conventionalcommits.org/):

```
feat: add new endpoint for batch processing
fix: resolve WiFi reconnection timeout
docs: update quick-start guide
chore: clean up unused imports
refactor: extract audio scaling into helper
```

Keep the subject line under 72 characters.

## Security

If you find a security vulnerability, do NOT open a public issue. See [SECURITY.md](SECURITY.md) for reporting instructions.
