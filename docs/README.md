# Reference

The pages here answer "how exactly does X work". They group into three:

- **Running it** — [deployment](deployment.md), [environment](environment.md),
  [troubleshooting](troubleshooting.md)
- **Calling it** — [api](api.md), [security](security.md)
- **Changing it** — [development](development.md), [ui](ui.md)

| | Page | Answers |
| :--- | :--- | :--- |
| 1 | [api.md](api.md) | What are the nine response headers and what does each mean? What does a report look like, field by field? What are the eighteen verifier routes? What does `degraded` mean? What is `x-chronos-internal` for? |
| 2 | [environment.md](environment.md) | Which of the 44 variables do I need? Which file is read first? Which model does which job and why two? How do I stay inside a free tier? What is the smallest `.env` that turns everything on? |
| 3 | [deployment.md](deployment.md) | What are the five containers? Why does the image build Bifrost from source? Why must the gateway not be statically linked? Why is the binary called `/app/main`? What does not work — Kubernetes, TLS, more than one replica? |
| 4 | [development.md](development.md) | What do I need installed? Which file owns what? What are the test suites and what does each need? How do I add a volatility class, a claim pattern, a scoring term, a route? What do I rebuild after changing what? |
| 5 | [security.md](security.md) | What is protected, and what is deliberately not? Why is Bifrost's SSRF guard off? What happens when an attacker writes the page the judge reads? What has to change before this is on a shared network? |
| 6 | [ui.md](ui.md) | What are the seven sections and what does each read? What is live mode against demo mode? What do the colours mean? Why are there two drawings of the mark? How is it checked, and what do those checks still not cover? |
| 7 | [troubleshooting.md](troubleshooting.md) | Which failures are quiet? What does this exact error string mean? Why is everything `unverifiable`? Why did eviction return 403? How do I tell "the verifier is down" from "the verifier is rate limited"? |

Alongside them: [architecture.html](architecture.html) and the
[architecture.png](architecture.png) rendered from it, the
[screenshots](screenshots/) with the shot list that regenerates them, and
[icon.png](icon.png).

## Start here instead

| | |
| :--- | :--- |
| You are deciding whether this works | [../TESTING.md](../TESTING.md) |
| You want to know how it works | [../ARCHITECTURE.md](../ARCHITECTURE.md) |
| You want every decision, and why | [../DESIGN.md](../DESIGN.md) |
| You are reviewing the plugin for upstream | [../plugin/README.md](../plugin/README.md) |
| You are a coding agent about to change something | [../AGENTS.md](../AGENTS.md) |
| You want the argument, the measurements and the limits | [../README.md](../README.md) |

Nothing on these pages repeats the README. Results live there; these pages link to them.
