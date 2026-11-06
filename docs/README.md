# Architecture

LiveContainer hosts the interface and guest apps. Embedded SideStore handles
accounts, provisioning, installation and refresh through a bounded service bridge.
Normal use stays in Home, Apps, Sources, Refresh and Settings.

## Components

- **LiveContainer:** host navigation, guest containers and guest lifecycle.
- **SideStore:** app database, account and installation/refresh operations.
- **SideSign / AnisetteKit:** signing, authentication and anisette integration.
- **minimuxer / idevice / jktcp:** device transport and native interfaces.

The supported same-device refresh route is LocalDevVPN, Lockdown,
CoreDeviceProxy, CDTunnel and RSD. Wi-Fi and device-specific pairing are required;
a VPN connection alone does not establish that refresh succeeded.

## Repository responsibilities

Application source lives in the component repositories. This repository contains
build and packaging tools, regression tests and source/artifact validation.
Current integration contracts and validation inputs live under `ci/`.

See [installation and updates](../README.md), [build entry points](../ci/README.md)
and [contributing](../CONTRIBUTING.md). Each release records its exact source and
artifact identity; tests for one source graph do not qualify a different build.
