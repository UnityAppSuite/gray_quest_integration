### GrayQuest

Gray Quest ERPNext Integration

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd frappe-bench
bench get-app https://github.com/WalnutAppSuite/gray_quest_integration.git --branch main
bench install-app grayquest
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/grayquest
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

apache-2.0
