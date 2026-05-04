# Contributing to OpenCharm

Thanks for your interest in contributing. This is a solo-developed project so response times may vary, but all contributions are welcome.

## Reporting bugs

Open a GitHub issue using the **Bug Report** template. Include:

- What you expected vs what happened
- Steps to reproduce
- Hardware details (ESP32 board revision, camera module)
- Serial monitor output or relay server logs
- Firmware version (git commit hash)

## Submitting code

1. Fork the repo
2. Create a feature branch from `main`
3. Make your changes
4. Test on hardware if touching firmware
5. Open a pull request against `main`

Keep PRs focused on one thing. A bug fix and a new feature should be separate PRs.

## Coding conventions

**Firmware (C++/Arduino)**
- PlatformIO build system
- 4-space indentation
- `camelCase` for variables and functions
- `UPPER_CASE` for constants and defines
- Keep `main.cpp` under 1000 lines; extract utilities into separate files if needed

**Relay server (Python)**
- Python 3.10+
- Follow PEP 8
- Use `async/await` for I/O operations
- Load secrets from environment variables, never hardcode

**Documentation**
- Markdown files in `docs/`
- Keep language direct and concise

## Commit messages

Use conventional commits:

```
feat: add new endpoint for batch processing
fix: correct audio buffer overflow on long recordings
docs: update quick-start guide with new relay setup
chore: update dependencies
refactor: extract LED state machine into separate file
```

Keep the subject line under 72 characters. Use the body for context if needed.

## Security

If you find a security vulnerability, do NOT open a public issue. See [SECURITY.md](SECURITY.md) for reporting instructions.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
