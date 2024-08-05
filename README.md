# fromager

Fromager is a tool for completely re-building a dependency tree of
Python wheels from source.

The goals are to support guaranteeing

1. The [binary
   package](https://packaging.python.org/en/latest/glossary/#term-Built-Distribution)
   someone is installing was built from source in a known build
   environment compatible with their own environment
1. All of the package’s dependencies were also built from source -- any
   binary package installed will have been built from source
1. All of the build tools used to build these binary packages will
   also have been built from source
1. The build can be customized for the packager's needs, including
   patching out bugs, passing different compilation options to support
   build "variants", etc.

The basic design tenet is to automate everything with a default
behavior that works for most PEP-517 compatible packages, but support
overriding all of the actions for special cases, without encoding
those special cases directly into fromager.

## Modes

Fromager has different modes for bootstrapping and production builds.
The bootstrap mode recursively processes all dependencies starting
from the requirements specifications given to determine what needs to
be built and what order to build it. The production build commands
separate these steps and avoid recursive processing so each step can
be performed in isolation.

## Bootstrapping

The `bootstrap` command

* Creates an empty package repository for
  [wheels](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)
* Downloads the [source
  distributions](https://packaging.python.org/en/latest/glossary/#term-Source-Distribution-or-sdist)
  for the input packages and places them under
  `sdists-repo/downloads/`.
* Recurses through the dependencies
  * Firstly, any build system dependency specified in the
    pyproject.toml build-system.requires section as per
    [PEP517](https://peps.python.org/pep-0517)
  * Secondly, any build backend dependency returned from the
    get_requires_for_build_wheel() build backend hook (PEP517 again)
  * Lastly, any install-time dependencies of the project as per the
    wheel’s [core
    metadata](https://packaging.python.org/en/latest/specifications/core-metadata/)
    `Requires-Dist` list.
* As each wheel is built, it is placed in a [PEP503 "simple" package
  repository](https://peps.python.org/pep-0503/) under
  `wheels-repo/simple` generated by
  [pypi-mirror](https://pypi.org/project/python-pypi-mirror/).
* The order the dependencies need to be built bottom-up is written to
  `build-order.json`.

Wheels are built by running `pip wheel` configured so it will only
download dependencies from the local wheel repository. This ensures
that all dependencies are being built in the correct order.

## Production Builds

Production builds use separate commands for the steps described as
part of bootstrapping, and accept arguments to control the servers
that are used for downloading source or built wheels.

### All-in-one commands

Two commands support building wheels from source.

The `build` command takes as input the distribution name and version
to build, the variant, and the URL where it is acceptable to download
source distributions. The server URL is usually a simple index URL for
an internal package index. The outputs are one patched source
distribution and one built wheel.

The `build-sequence` command takes a build-order file, the variant,
and the source distribution server URL. The outputs are patched source
distributions and built wheels for each item in the build-order file.

### Step-by-step commands

Occasionally it is necessary to perform additional tasks between build
steps, or to run the different steps in different configurations (with
or without network access, for example). Using the `step` subcommands,
it is possible to script the same operations performed by the `build`
and `build-sequence` commands.

The `step download-source-archive` command finds the source
distribution for a specific version of a dependency on the specified
package index and downloads it. It will be common to run this step
with `pypi.org`, but for truly isolated and reproducible builds a
private index server is more robust.

The `step prepare-source` command unpacks the source archive
downloaded from the previous step and applies any patches (refer to
[customization](docs/customization.md) for details about patching).

The `step prepare-build` command creates a virtualenv with the build
dependencies for building the wheel. It expects a `--wheel-server-url`
as argument to control where built wheels can be downloaded.

The `step build-sdist` command turns the prepared source tree into a
new source distribution ("sdist"), including any patches or vendored
code.

The `step build-wheel` command creates a wheel using the build
environment and prepared source, compiling any extensions using the
appropriate override environment settings (refer to
[customization](docs/customization.md) for details about overrides).

## Using private registries

Fromager uses the [requests](https://requests.readthedocs.io) library and `pip`
at different points for talking to package registries. Both support
authenticating to remote servers in various ways. The simplest way to integrate
the authentication with fromager is to have a
[netrc](https://docs.python.org/3/library/netrc.html) file with a valid entry
for the host. The file will be read from `~/.netrc` by default. Another location
can be specified by setting the `NETRC` environment variable.

For example, to use a gitlab package registry, use a [personal
access
token](https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html#create-a-personal-access-token)
as documented in [this
issue](https://gitlab.com/gitlab-org/gitlab/-/issues/350582):

```
machine gitlab.com login oauth2 password $token
```  

## Determining versions via GitHub tags  

In some cases, the builder might have to use tags on GitHub to determine the version of a project instead of looking at
pypi.org. To avoid rate limit or to access private GitHub repository, a [personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) can be passed to fromager by setting
the following environment variable:  

```shell
GITHUB_TOKEN=<access_token>
```

## Additional docs

* [Package build customization instructions](docs/customization.md)
* [Developer instructions](docs/develop.md)

## What's with the name?

Python's name comes from Monty Python, the group of comedians. One of
their skits is about a cheese shop that has no cheese in stock. The
original Python Package Index (pypi.org) was called The Cheeseshop, in
part because it hosted metadata about packages but no actual
packages. The wheel file format was selected because cheese is
packaged in wheels. And
"[fromager](https://en.wiktionary.org/wiki/fromager)" is the French
word for someone who makes or sells cheese.
