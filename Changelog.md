# Changelog

## 0.1.3 (2025-02-21)

### Added
- Added `as_file_target_folder` parameter for specifying screenshot save location
- Added validation for target folder existence and write permissions
- Added automatic folder creation if target folder doesn't exist

## 0.1.2 (2025-02-21)

### Changed
- Replaced `include_screenshot` boolean parameter with `return_screenshot` enum
- Added new screenshot return modes: "none", "as_file", "as_base64", "as_imagecontent", "as_imageurl"
- Added parameter validation for return_screenshot enum with detailed error messages
- Moved logging level configuration to environment variables
- Removed `experimental` parameter in favor of explicit return modes
- Updated documentation with new screenshot return format examples

## 0.1.1 (2025-02-20)

### Changed
- Modified SAP GUI tools to include an optional `include_screenshot` parameter (default: `false`). Screenshots are no longer returned by default.
- Updated `save_last_screenshot` tool to return the absolute file path of the saved screenshot.

## 0.1.0 (Initial Release)
- Initial Release
