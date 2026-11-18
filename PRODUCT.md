# Adrobot

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI, SQLAlchemy and Alembic backend. Frontend implementation delegated by the assignment; selected React, TypeScript and Vite for an interactive editor. SQLite for portable local persistence. Root compose.yml for local launch.

## Users

Advertising operators creating Keitaro campaigns and adjusting offer distribution. Hiring reviewers reproducing the reference workflow.

## Product Purpose

Create a campaign with a country-filtered Google redirect and an offer stream; safely prepare, cancel and publish offer changes.

## Operating Context

Local application connected to a remote Keitaro Admin API. Campaign names use test_borkoev_ by default. Keitaro credentials are server-only environment variables and never committed.

## Capabilities and Constraints

Campaign creation includes domain, group and traffic source. Editor imports all streams, treats non-offer streams as opaque, persists drafts, archives removed associations, restores offers, synchronizes external deletions and fixes pinned shares while distributing the remainder to total 100 percent. The reference video governs these behaviors.

## Evidence on Hand

Public Notion assignment: https://app.notion.com/p/mnovikov/Adrobot-2b01eb78a7bb80a1b188c3a4a1cad55e
Reference video: https://youtu.be/jNloscEWCjA

## Product Principles

Draft changes stay local until an explicit publish action. Cancel restores the published state. Unknown stream settings are preserved. Invalid totals and concurrent edits are rejected. Credentials stay on the backend.
