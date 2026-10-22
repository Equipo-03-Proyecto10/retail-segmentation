//! The staged contract (CI's stage-contracts.sh copies it into contracts/)
//! against the service's own parsing and rendering: request examples read as
//! the same ids in JSON and XML, and a rendered profile is the example's.

use std::fs;

use customer_preferences::{submitted_ids, PreferenceSet, Profile, SERVICE};
use mosaiq_kit::rendering::{render_json, render_xml};
use mosaiq_kit::{Contract, Reply};
use serde_json::Value as JsonValue;
use uuid::Uuid;

const NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/customer-preferences/v1";

fn contract() -> Contract {
    Contract::load("contracts", SERVICE).expect(
        "the contract is staged: bash .github/scripts/stage-contracts.sh customer-preferences",
    )
}

fn example(name: &str) -> Vec<u8> {
    fs::read(format!("contracts/{SERVICE}/examples/{name}")).unwrap()
}

fn json_example(name: &str) -> JsonValue {
    serde_json::from_slice(&example(name)).unwrap()
}

/// The document without its declaration, inter-element whitespace or the
/// kit's unused `xmlns:common` declaration, which change no element.
fn compact(xml: &str) -> String {
    let mut out = String::new();
    for line in xml.lines() {
        out.push_str(line.trim());
    }
    let body = out.split_once("?>").map_or(out.as_str(), |(_, rest)| rest);
    body.replace(
        " xmlns:common=\"https://mosaiq.maxthecoder.online/xml/common/v1\"",
        "",
    )
}

fn ids(value: &JsonValue, field: &str) -> Vec<i16> {
    value[field]
        .as_array()
        .unwrap()
        .iter()
        .map(|id| i16::try_from(id.as_i64().unwrap()).unwrap())
        .collect()
}

#[test]
fn a_rendered_profile_is_the_example_in_json_and_xml() {
    for name in [
        "get-profile",
        "get-empty-profile",
        "replace-preferred-channels",
        "replace-interest-categories",
        "clear-preferred-channels",
        "clear-interest-categories",
    ] {
        let expected = json_example(&format!("{name}.json"));
        let profile = Profile {
            customer_id: Uuid::parse_str(expected["customer_id"].as_str().unwrap()).unwrap(),
            preferred_channel_ids: ids(&expected, "preferred_channel_ids"),
            interest_category_ids: ids(&expected, "interest_category_ids"),
        };
        let reply = Reply::from(profile.representation());
        assert_eq!(render_json(&reply, 20).unwrap(), expected, "{name}.json");
        let xml = String::from_utf8(example(&format!("{name}.xml"))).unwrap();
        assert_eq!(
            compact(&render_xml(&reply, NAMESPACE, 20).unwrap()),
            compact(&xml),
            "{name}.xml"
        );
    }
}

#[test]
fn request_examples_read_as_the_same_ids_in_json_and_xml() {
    let contract = contract();
    for (operation, set, name) in [
        (
            "replacePreferredChannels",
            PreferenceSet::PreferredChannels,
            "replace-preferred-channels",
        ),
        (
            "replaceInterestCategories",
            PreferenceSet::InterestCategories,
            "replace-interest-categories",
        ),
    ] {
        let operation = contract.operation(operation).unwrap();
        let json = contract
            .parse_json(&example(&format!("{name}.request.json")))
            .unwrap();
        contract.validate(&json, operation).unwrap();
        let xml = contract
            .parse_xml(&example(&format!("{name}.request.xml")), operation)
            .unwrap();
        contract.validate(&xml, operation).unwrap();
        let from_json = submitted_ids(set, &json).unwrap();
        assert!(!from_json.is_empty());
        assert_eq!(submitted_ids(set, &xml).unwrap(), from_json, "{name}");
    }
}

#[test]
fn an_empty_json_list_is_a_valid_clear() {
    let contract = contract();
    for (operation, set, name) in [
        (
            "replacePreferredChannels",
            PreferenceSet::PreferredChannels,
            "clear-preferred-channels.request.json",
        ),
        (
            "replaceInterestCategories",
            PreferenceSet::InterestCategories,
            "clear-interest-categories.request.json",
        ),
    ] {
        let operation = contract.operation(operation).unwrap();
        let body = contract.parse_json(&example(name)).unwrap();
        contract.validate(&body, operation).unwrap();
        assert!(submitted_ids(set, &body).unwrap().is_empty());
    }
}

#[test]
fn the_invalid_xml_examples_are_refused() {
    let contract = contract();
    for (operation, name) in [
        (
            "replacePreferredChannels",
            "replace-preferred-channels-with-text.invalid.xml",
        ),
        (
            "replaceInterestCategories",
            "replace-interest-categories-with-unknown-element.invalid.xml",
        ),
    ] {
        let operation = contract.operation(operation).unwrap();
        let error = contract.parse_xml(&example(name), operation).unwrap_err();
        assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
    }
}

/// Known kit gap, kept strict so it flips when the kit is fixed: kit-rs
/// v0.1.0 reads an XML request with no id elements as an object without the
/// member, so the OpenAPI `required` refuses the contract's own XML clear
/// example with 422 before the handler runs. JSON clears work. This is the
/// family of #571 (an absent nullable member) and #585 (`xsi:nil`); the fix
/// belongs in the kit (an absent array member reads as `[]`), then this test
/// is inverted to expect the clear.
#[test]
fn kit_gap_an_xml_clear_is_still_refused_by_kit_rs_v0_1_0() {
    let contract = contract();
    for (operation, field, name) in [
        (
            "replacePreferredChannels",
            "channel_ids",
            "clear-preferred-channels.request.xml",
        ),
        (
            "replaceInterestCategories",
            "category_ids",
            "clear-interest-categories.request.xml",
        ),
    ] {
        let operation = contract.operation(operation).unwrap();
        let body = contract.parse_xml(&example(name), operation).unwrap();
        let error = contract.validate(&body, operation).unwrap_err();
        assert_eq!((error.status, error.code.as_str()), (422, "invalid_body"));
        assert_eq!(error.details[0].field, field);
        assert_eq!(error.details[0].code, "required");
    }
}
