# Product

**Status:** Current product principles for the active root application. See [architecture](docs/architecture.md) and [known limitations](docs/known-limitations.md) for implementation detail.

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

SIGNIX primarily serves Indian homeowners evaluating rooftop solar for a specific property. Their first job is to locate the property accurately, define the roof that they control, and understand a preliminary system, generation, subsidy, and financial estimate.

## Product Purpose

SIGNIX combines property discovery, an authoritative user-drawn roof boundary, system sizing, generation modelling, and indicative financial analysis in one guided workflow. Success means helping a homeowner form a defensible planning view without presenting simulated or incomplete data as verified site evidence.

## Positioning

The product connects a user-controlled rooftop footprint to solar and financial planning. The manually selected roof remains authoritative while geographic providers and AI can add context without silently replacing saved user inputs.

## Operating Context

The core flow is: locate a property, draw or confirm the roof, mark obstructions, configure the system, and review a saved report. The application is a Flask web app with a Leaflet map, Three.js rooftop visualization, and bilingual English/Hindi navigation.

## Capabilities and Constraints

- GeoJSON persistence uses longitude/latitude order; Leaflet uses latitude/longitude.
- Estimates are preliminary and depend on available location, roof, irradiance, and user-supplied information.
- Location discovery may use existing free geocoding providers and parsed Google Maps links; it must not silently substitute a guessed property.
- Solar calculations, financial assumptions, authentication, roof reconstruction, and shade classification are established systems that visual work must preserve.
- No paid location or Google Solar API dependency is required for the first-use flow.

## Brand Commitments

The product name is SIGNIX. Its voice is direct, calm, technically credible, and careful about what is measured, simulated, or indicative.

## Evidence on Hand

The repository contains working estimate, report, location, map drawing, rooftop reconstruction, and provider-ingestion flows. It does not contain substantiated universal savings, payback, accuracy, or subsidy-eligibility claims, so future marketing must remain qualified.

## Product Principles

- Keep the map and the user-selected property central.
- State data provenance and uncertainty plainly.
- Preserve user-authored roof and financial inputs.
- Prefer resilient, recoverable workflows over silent fallbacks.
- Make technical planning information understandable without diluting it.

## Accessibility & Inclusion

The web experience must support keyboard navigation, visible focus, reduced motion, responsive layouts, and status messaging that does not rely on color alone. Existing English/Hindi language switching is preserved.
