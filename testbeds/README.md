# Testbeds

Each testbed keeps its device models, router and switch configuration, and
versioned datasets together:

```text
testbeds/<testbed>/
├── devices/
├── router/
├── switch/
└── datasets/<version>/
```

[Metropolis](metropolis/README.md) is the current water-treatment testbed.
Shared document formats live in [schemas/](../schemas/); each dataset chooses
its own nodes, addresses, and links.
