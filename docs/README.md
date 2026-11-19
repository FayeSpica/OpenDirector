# OpenDirector documentation

This directory is the entry point for the repository's shared documentation. The documents below are tracked with the code; developers do not need local historical material, chat records, or neighboring repositories to use them.

## Reading order and responsibilities

| Document | What it covers | When to update it |
| --- | --- | --- |
| [Development guidance](../AGENTS.md) | Product direction and enduring principles | Architecture principles or product boundaries change |
| [Contributing](../CONTRIBUTING.md) | Label taxonomy, how to pick labels, and what a PR must satisfy | Process, labels, or submission requirements change |
| [Unified project architecture](architecture/unified-project.md) | Business objects, shared entry points, and interaction rules | Object responsibilities, commands, persistence, or interaction rules change |
| [Desktop architecture](architecture/desktop.md) ([中文](architecture/desktop.zh-CN.md)) | Electron, the Python Host, project storage, and shutdown behavior | Process, request, storage, or lifecycle boundaries change |
| [Test contracts](architecture/test-contracts.md) | Test/production boundaries, how to avoid stale assertions and environment-dependent red runs | Test infrastructure, capability gates, or contract ownership change |
| [Capability mapping contract](architecture/capability-contract.md) | Evidenced upstream vocabulary, the `(operation, profile) → capability` table, the untranslatable declarations, and how it feeds discovery | Upstream vocabulary, capability ids, or discovery matching change |
| [Roadmap](development/roadmap.md) | Capability dependencies and delivery criteria | Scope, dependencies, or acceptance criteria change |
| [Validation guide](development/validation.md) | Reproducible checks and evidence requirements | Tools, runtime requirements, or validation entry points change |
| [CI layer assessment](development/ci-layer-assessment.md) | Timing evidence, critical PR representatives and staged Python classification | Measured evidence or classification decisions change |
| [Issue #87 Windows handoff](development/issue-87-windows-handoff.md) ([中文](development/issue-87-windows-handoff.zh-CN.md)) | Native ACL repair and final text acceptance | The shared checkpoint or native validation scope changes |
| [Development lessons](development/lessons.md) | Risks identified from recent fixes | New evidence emerges or a lesson no longer applies |
| [CI failure patterns](development/ci-failures.md) | Known timing-sensitive CI failures and how to tell flakes from regressions | A pattern is fixed, newly observed, or the watchlist changes |
| [Release tags and daily cuts](development/release.md) | Public SemVer tags and the daily patch cut | The tag scheme, daily cut gate, or version-field set changes |
| [UI performance design](development/ui-performance.md) | Interaction design principles, latency budgets, and measurement | A UI performance principle, target, or measurement method changes |
| [macOS installation validation](development/macos-install-validation.md) | Installation and usage scenarios verified on Apple Silicon | The corresponding acceptance checks are repeated |
| [Bilingual interface](development/i18n.md) | Language selection, message organization, and checks | Language policy, message structure, or checks change |
| [Python installation and packaging](development/packaging.md) | Installation, platform wheel builds, and isolated verification | Packaging, resource paths, or build process changes |
| [Business CLI](guides/cli.md) | Querying, editing, and generating through a terminal or Agent Shell | Commands, authorization, or receipt semantics change |
| [DeepSeek Harness plugin](../plugins/deepseek-harness/README.md) | Installing the DSH bundle and editing an OpenDirector project from DSH | Plugin setup, tools, or creation workflows change |
| [Video model capabilities](guides/video-model-capabilities.md) ([中文](guides/video-model-capabilities.zh-CN.md)) | Declaring output specifications supported by a service | Configuration fields or capability integration changes |
| [Model availability / 模型可用状态](guides/model-availability.md) | Optional runtime declarations, automatic checks, shared admission and API/CLI/Agent queries | Capability protocol, refresh, or acceptance rules change |
| [OpenAI Images](guides/openai-images.md) | Wiring gpt-image as an optional image service, request shape and failures | Integration fields, capability scope, or the OpenAI interface change |
| [Kling video](guides/kling-video.md) ([中文](guides/kling-video.zh-CN.md)) | Optional Kling 4.0 and Flash setup, model-specific inputs, and recovery limits | Kling inputs, model declarations, or recovery behavior change |
| [BigModel video](guides/bigmodel-video.md) ([中文](guides/bigmodel-video.zh-CN.md)) | CogVideoX-3 text, single-image and first/last-frame generation, task recovery and adoption | BigModel protocol, inputs, output sizes or recovery behavior change |
| [BigModel GLM-Image](guides/bigmodel-image.md) ([中文](guides/bigmodel-image.zh-CN.md)) | Account setup, text-to-image generation, adoption and recovery | BigModel integration fields or behavior change |
| [GLM-Image validation](development/glm-image-validation.md) | Real provider workflow screenshots and automated checks | GLM-Image acceptance is repeated |
| [Desktop application](development/desktop.md) ([中文](development/desktop.zh-CN.md)) | Desktop build, CLI registration, and release checks | Build, CLI registration, or release checks change |
| [Agent runtime](architecture/agent-runtime.md) | Agent runtime architecture and Job origin tracing | Runtime architecture or Job origin semantics change |
