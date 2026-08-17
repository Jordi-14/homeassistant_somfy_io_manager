# Contributing

Bug reports and focused pull requests are welcome.

Before opening an issue:

- confirm the bridge is running a compatible manager API;
- reproduce the problem on the latest released integration;
- remove credentials, controller keys, recovery blobs, remote identifiers, and
  home network details from logs.

Before opening a pull request, run:

```bash
ruff check .
pytest -q
```

Keep radio-protocol and firmware changes in the ESPHome component repository.
This repository owns only the Home Assistant integration and its documented
manager API client.
