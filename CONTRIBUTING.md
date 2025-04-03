# Contributing to Vuegraf

## Guidelines

PRs are welcome, but must include tests, when possible. PRs will not be merged if they do not pass
the automated CI workflows. To test your changes before creating a PR, run
`sudo make clean; sudo make test-docker` from the root of the repository (requires Docker to be
running on your machine).

Make sure you follow the existing coding style from the existing codebase. Do not reformat the existing code to fit your own personal style.

Before submitting the PR review that you have included the following changes, where applicable:
- Unit Tests: Must cover all new logic paths
- Documentation: If you're adding new functionality, any new configuration options should be documented appropriately in the [README.md](README.md).
- Changelog: Describe your contribution to the appropriate section(s) for the _Upcoming release_, in the [CHANGELOG.md](CHANGELOG.md) file.

## Releases

STOP - DO NOT PROCEED! This section is only applicable to project administrators. PR _contributors_ do not need to follow the below procedure.

As Vuegraf is a community-maintained project, releases will typically contain unrelated contributions without a common theme. It's up to the maintainers to determine when the project is ready for a release, however, if you are looking to use a newly merged feature that hasn't yet been released, feel free to open a [discussion][4] and let us know.

Maintainers, when creating a new release, follow the procedure below:

1. Determine an appropriate new version number in the format _a.b.c_, using the following guidelines:
	- The major version (a) should not change.
	- The minor version (b) should be incremented if a new feature has been added or if a bug fix will have a significant user-impact. Reset the patch version to zero if the minor version is incremented.
	- The patch version (c) should be incremented when low-impact bugs are fixed, or security vulnerabilities are patched.
2. Ensure the following are updated _before_ publishing/tagging the new release:
	- [setup.py](setup.py): Match the version to the new release version
	- [vuegray.py](src/vuegraf/vuegraf.py): Match the version to the new release version.
	- [CHANGELOG.md](CHANGELOG.md): This must contain all PRs and any other relevent notes about this release
3. Publish a [new][1] release.
	- The title (and tag) of the release will be the same value as the new version determined in step 1.
	- Paste the new version change notes from CHANGELOG.md into the description field.
	- Check the box to 'Create a discussion for this release'.
4. Verify that artifacts have been published:
 	- Python PIP package was [published][3] successfully.
 	- Container image was [published][2] successfully.

[1]: https://github.com/jertel/vuegraf/releases/new
[2]: https://github.com/jertel/vuegraf/actions/workflows/publish_image.yml
[3]: https://github.com/jertel/vuegraf/actions/workflows/python-publish.yml
[4]: https://github.com/jertel/vuegraf/discussions
