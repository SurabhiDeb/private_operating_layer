# Wayfinder — service definition

Wayfinder suggests a next action to a field engineer from a work order and a photo.
This document is the contract. Where it disagrees with the code, this document is right.

## 1) What it will not do

- It will not dispatch an engineer or change a schedule.
- It will not contact a customer.
- It will not price a job.

## 2) Response shape

Every reply is a JSON object with `action`, `confidence` and `because`. A reply that
cannot be parsed is a failure of the whole call, not a low score.

## 3) Service levels

The suggestion must be correct for at least 92% of work orders.

Where the photo is unusable, Wayfinder must say so rather than guess; it must do this
on no less than 95% of unusable photos.

Median round trip should stay under 1500ms, and the 95th percentile under 4 seconds.

Spend must remain below £900 per month.

Repeat-suggestion rate wants to sit between 4% and 11%. Too low and it is being timid,
too high and it is looping.

## 4) Signals we watch

| Signal | Bar | Commentary |
|---|---|---|
| Accepted suggestions | at least 60% | Engineers accept it often enough to be worth opening |
| Escalations to a human | no more than 25% | Deliberately generous while the corpus is thin |

## 5) Situations that have bitten us

1. A photo of a different unit entirely.
2. Two work orders merged into one ticket.
3. A part number that was renamed last quarter.
