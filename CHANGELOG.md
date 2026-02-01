[1.3.3]: https://github.com/Sundancer78/proxmox-suits/releases/tag/v1.3.3

## [1.3.3] – 2026-02-01

### Changed
- Normalize Proxmox Backup Server (PBS) device naming by removing IP addresses from display names.
- Use hostname-based naming for PBS (`PBS`) to ensure consistent and clean device titles.
- Remove measurement units (e.g. `GiB`) from sensor names.
- Display units exclusively via Home Assistant’s native unit handling (`native_unit_of_measurement`) to avoid duplicate unit display in the UI.

### Notes
- Cosmetic / UI-focused release only.
- No entity IDs, sensor logic, or API behavior were changed.
- Users with previously configured duplicate PBS entries (IP-based and `localhost`) may need to remove old entries and re-add PBS once to achieve consistent naming.
