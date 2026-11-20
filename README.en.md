# AlfalfaCode

[Español](README.md) · English

AlfalfaCode is an open-source coding agent built in Peru. Use it from a native
SwiftUI app on macOS or from the terminal with `alfalfa`. Open a repository, give
the agent a task, and review its work. You can pick a model and control what the
agent can run.

## Install

The Mac app requires macOS 14 or later. Download the signed and notarized DMG
for your Mac, then drag AlfalfaCode into Applications.

- [Apple Silicon DMG](https://github.com/0sw2ld0/AlfalfaCode/releases/latest/download/AlfalfaCode.dmg)
- [Intel DMG](https://github.com/0sw2ld0/AlfalfaCode/releases/latest/download/AlfalfaCode-intel.dmg)

On Apple Silicon, Homebrew can install the app and the CLI separately:

```sh
brew install --cask 0sw2ld0/tap/alfalfacode
brew install 0sw2ld0/tap/alfalfa
```

The app bundles its own engine. For the CLI on either Mac architecture, you can
also use the [standalone installer](docs/guia/instalar.md):

```sh
curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh
```

The CLI also runs on [Ubuntu 24.04 and WSL2](docs/USO.md#linux), with bubblewrap
for command isolation.

## Try your first task

In the app, open a repository and ask the agent to explain how the project is
tested. From the terminal, run this from the repository directory:

```sh
alfalfa "Explain how to run this project's tests."
```

Use the model picker to select a provider. AlfalfaCode supports Ollama locally
or in the cloud, OpenAI, Anthropic, OpenRouter, OpenCode Zen, and other
OpenAI-compatible providers. The picker also lists currently available free
models. To connect OpenRouter from the CLI:

```sh
alfalfa --conectar openrouter
```

The Alfalfa provider can also be activated with a CUY courtesy code. On Intel
Macs, that provider requires a T2 chip.

See the [first-run guide](docs/guia/primera-vez.md), [provider setup](docs/guia/proveedores.md),
and [model pricing and budgets](docs/guia/catalogo.md). The detailed guides are
in Spanish.

## Try the approval flow

1. Open a disposable project folder in the Mac app and choose manual mode.
2. Ask the agent to run `mkdir pacai-demo-marker`. Stop the turn when its permission prompt appears.
3. Send a new task in the same session, such as asking the agent to list the project's files.

By default, macOS commands run in the Seatbelt sandbox with network access
blocked. Manual mode asks before file edits and commands that change state. The
[permissions guide](docs/guia/permisos.md) describes each mode.

## What you can do

- Run parallel subagents and queue messages while the agent is working. See [subagents](docs/guia/subagentes.md) and [message queues](docs/guia/ocupado.md).
- Resume or fork a session, search local session history, and restore earlier project states. See [sessions](docs/guia/sesiones.md) and [undo](docs/guia/deshacer.md).
- Bring project instructions, skills, hooks, and MCP servers into the workflow. See [migration](docs/guia/migrar.md) and [skills and memory](docs/guia/habilidades.md).
- Use the Mac app's browser and app testing tools. See the [browser guide](docs/guia/navegador.md) and [Mac app testing](docs/guia/apps-mac.md).

## Integrate with PACAI

Launch `alfalfa --pacai` from your client to exchange PACAI 0.3 messages over
stdin and stdout. The Mac app uses the same interface for its agent sessions.
The [PACAI integration guide](docs/USO.md#control-con-pacai-alfalfa---pacai)
covers the handshake, tool approvals, and the `alfalfa` extension.

## More information

- [Documentation index in Spanish](docs/guia/README.md)
- [Building and packaging the Mac app](alfalfa-mac/EMPAQUETADO.md)
- [Tests](docs/guia/pruebas.md)
- [Release notes and downloads](https://github.com/0sw2ld0/AlfalfaCode/releases)

AlfalfaCode is released under the [MIT License](LICENSE). See the
[Spanish README](README.md#procedencia) for third-party credits.
