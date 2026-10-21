//! Staged OpenAPI and XSD loading, routing metadata, and body validation.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock};

use bytes::Bytes;
use indexmap::IndexMap;
use jsonschema::error::ValidationErrorKind;
use jsonschema::paths::LocationSegment;
use libxml::parser::{Parser, ParserOptions};
use libxml::schemas::{SchemaParserContext, SchemaValidationContext};
use quick_xml::events::Event;
use quick_xml::name::ResolveResult;
use quick_xml::NsReader;
use regex::bytes::Regex as BytesRegex;
use regex::Regex;
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value as JsonValue};

use crate::types::{ApiError, Detail};

static FORBIDDEN_XML: LazyLock<BytesRegex> =
    LazyLock::new(|| BytesRegex::new(r"(?i)<!\s*(?:DOCTYPE|ENTITY)\b").expect("valid regex"));
static XSD_ELEMENT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"Element '(?:\{[^}]+\})?([^']+)'").expect("valid regex"));
static XSD_MISSING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"Expected is(?: one of)? \( (?:\{[^}]+\})?([\w.-]+)").expect("valid regex")
});
const PREDEFINED_ENTITIES: [&str; 5] = ["lt", "gt", "amp", "apos", "quot"];

/// A contract that cannot be loaded, an operation that cannot be registered,
/// or configuration the kit cannot use.
#[derive(Debug)]
pub struct KitError(pub String);

impl fmt::Display for KitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for KitError {}

fn kit_error(message: impl Into<String>) -> KitError {
    KitError(message.into())
}

/// How a path segment is matched, from its parameter schema.
#[derive(Clone, Debug)]
pub enum PathMatcher {
    Integer {
        minimum: Option<i64>,
        maximum: Option<i64>,
    },
    Uuid,
    Pattern(Regex),
    Text,
}

/// A typed path value handed to the handler.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PathValue {
    Integer(i64),
    Uuid(uuid::Uuid),
    Text(String),
}

impl PathMatcher {
    fn from_schema(schema: &JsonValue) -> Result<Self, KitError> {
        if schema.get("type").and_then(JsonValue::as_str) == Some("integer") {
            return Ok(PathMatcher::Integer {
                minimum: schema.get("minimum").and_then(JsonValue::as_i64),
                maximum: schema.get("maximum").and_then(JsonValue::as_i64),
            });
        }
        if schema.get("format").and_then(JsonValue::as_str) == Some("uuid") {
            return Ok(PathMatcher::Uuid);
        }
        if let Some(pattern) = schema.get("pattern") {
            let pattern = match pattern {
                JsonValue::String(text) => text.clone(),
                other => other.to_string(),
            };
            let inner = pattern.strip_prefix('^').unwrap_or(&pattern);
            let inner = inner.strip_suffix('$').unwrap_or(inner);
            let regex = Regex::new(&format!("^(?:{inner})$"))
                .map_err(|error| kit_error(format!("Invalid path pattern {pattern:?}: {error}")))?;
            return Ok(PathMatcher::Pattern(regex));
        }
        Ok(PathMatcher::Text)
    }

    /// Match one decoded segment; `None` is a routing miss (404).
    pub fn matches(&self, segment: &str) -> Option<PathValue> {
        if segment.is_empty() || segment.contains('/') {
            return None;
        }
        match self {
            PathMatcher::Integer { minimum, maximum } => {
                if !segment.bytes().all(|byte| byte.is_ascii_digit()) {
                    return None;
                }
                let value: i64 = segment.parse().ok()?;
                if minimum.is_some_and(|minimum| value < minimum)
                    || maximum.is_some_and(|maximum| value > maximum)
                {
                    return None;
                }
                Some(PathValue::Integer(value))
            }
            PathMatcher::Uuid => {
                let shape = segment.len() == 36
                    && segment.char_indices().all(|(index, character)| {
                        if matches!(index, 8 | 13 | 18 | 23) {
                            character == '-'
                        } else {
                            character.is_ascii_hexdigit()
                        }
                    });
                if !shape {
                    return None;
                }
                uuid::Uuid::parse_str(segment).ok().map(PathValue::Uuid)
            }
            PathMatcher::Pattern(regex) => regex
                .is_match(segment)
                .then(|| PathValue::Text(segment.to_owned())),
            PathMatcher::Text => Some(PathValue::Text(segment.to_owned())),
        }
    }
}

/// One OpenAPI operation, with everything the kit needs to serve it.
#[derive(Clone)]
pub struct Operation {
    pub operation_id: String,
    pub path: String,
    pub method: String,
    pub protected: bool,
    pub roles: Vec<String>,
    pub parameters: Vec<JsonValue>,
    pub request_schema: Option<JsonValue>,
    pub xml_name: Option<String>,
    pub xml_namespace: Option<String>,
    validator: Option<Arc<jsonschema::Validator>>,
}

impl fmt::Debug for Operation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Operation")
            .field("operation_id", &self.operation_id)
            .field("path", &self.path)
            .field("method", &self.method)
            .field("protected", &self.protected)
            .field("roles", &self.roles)
            .field("xml_name", &self.xml_name)
            .field("xml_namespace", &self.xml_namespace)
            .finish_non_exhaustive()
    }
}

impl Operation {
    pub fn paginated(&self) -> bool {
        self.parameters.iter().any(|parameter| {
            parameter.get("in").and_then(JsonValue::as_str) == Some("query")
                && matches!(
                    parameter.get("name").and_then(JsonValue::as_str),
                    Some("page" | "size")
                )
        })
    }

    /// The matcher for each `{name}` segment of the path, in order.
    pub fn path_matchers(&self) -> Result<Vec<(String, PathMatcher)>, KitError> {
        let schemas: HashMap<&str, &JsonValue> = self
            .parameters
            .iter()
            .filter(|parameter| parameter.get("in").and_then(JsonValue::as_str) == Some("path"))
            .filter_map(|parameter| {
                Some((
                    parameter.get("name")?.as_str()?,
                    parameter.get("schema").unwrap_or(&JsonValue::Null),
                ))
            })
            .collect();
        self.path
            .split('/')
            .filter_map(|segment| segment.strip_prefix('{')?.strip_suffix('}'))
            .map(|name| {
                let schema = schemas.get(name).copied().unwrap_or(&JsonValue::Null);
                Ok((name.to_owned(), PathMatcher::from_schema(schema)?))
            })
            .collect()
    }
}

fn non_empty_object(value: Option<&JsonValue>) -> Option<&JsonValue> {
    value.filter(|item| item.as_object().is_some_and(|members| !members.is_empty()))
}

fn media(content: Option<&JsonValue>) -> Option<&JsonValue> {
    let content = content?;
    non_empty_object(content.get("application/json"))
        .or_else(|| non_empty_object(content.get("application/xml")))
}

fn unescape_pointer(part: &str) -> String {
    part.replace("~1", "/").replace("~0", "~")
}

/// One service's staged OpenAPI and XML Schema.
pub struct Contract {
    pub root: PathBuf,
    pub service: String,
    pub openapi_bytes: Bytes,
    pub xsd_bytes: Bytes,
    pub xsd_path: PathBuf,
    pub namespace: String,
    pub document: JsonValue,
    pub operations: IndexMap<String, Operation>,
}

thread_local! {
    static XML_SCHEMAS: RefCell<HashMap<PathBuf, SchemaValidationContext>> =
        RefCell::new(HashMap::new());
}

fn with_xml_schema<T>(
    path: &Path,
    action: impl FnOnce(&mut SchemaValidationContext) -> T,
) -> Result<T, KitError> {
    XML_SCHEMAS.with(|cache| {
        let mut cache = cache.borrow_mut();
        if !cache.contains_key(path) {
            let location = path
                .to_str()
                .ok_or_else(|| kit_error("The service XSD path is not UTF-8."))?;
            let mut parser = SchemaParserContext::from_file(location);
            let context = SchemaValidationContext::from_parser(&mut parser).map_err(|errors| {
                let messages: Vec<String> = errors
                    .into_iter()
                    .filter_map(|error| error.message)
                    .collect();
                kit_error(format!(
                    "The service XSD does not compile: {}",
                    messages.join(" ")
                ))
            })?;
            cache.insert(path.to_owned(), context);
        }
        Ok(action(cache.get_mut(path).expect("cached schema")))
    })
}

impl Contract {
    pub fn load(contracts_dir: impl AsRef<Path>, service: &str) -> Result<Self, KitError> {
        let root = contracts_dir.as_ref().to_owned();
        let openapi_path = root.join(service).join("openapi.yaml");
        let xsd_path = root.join(service).join(format!("{service}.xsd"));
        let openapi_bytes = std::fs::read(&openapi_path)
            .map_err(|error| kit_error(format!("{}: {error}", openapi_path.display())))?;
        let xsd_bytes = std::fs::read(&xsd_path)
            .map_err(|error| kit_error(format!("{}: {error}", xsd_path.display())))?;
        let document: JsonValue = serde_yaml::from_slice(&openapi_bytes)
            .map_err(|error| kit_error(format!("{}: {error}", openapi_path.display())))?;
        let namespace = target_namespace(&xsd_bytes)
            .ok_or_else(|| kit_error("The service XSD has no targetNamespace."))?;
        let xsd_path = xsd_path.canonicalize().unwrap_or(xsd_path);
        with_xml_schema(&xsd_path, |_| ())?;
        let mut contract = Self {
            root,
            service: service.to_owned(),
            openapi_bytes: Bytes::from(openapi_bytes),
            xsd_bytes: Bytes::from(xsd_bytes),
            xsd_path,
            namespace,
            document,
            operations: IndexMap::new(),
        };
        contract.operations = contract.load_operations()?;
        Ok(contract)
    }

    fn resolve_ref(&self, reference: &str) -> Result<JsonValue, KitError> {
        let pointer = reference.strip_prefix("#/").ok_or_else(|| {
            kit_error(format!(
                "Only local contract references are supported: {reference:?}"
            ))
        })?;
        let mut value = &self.document;
        for part in pointer.split('/') {
            value = value
                .get(unescape_pointer(part))
                .ok_or_else(|| kit_error(format!("Unresolvable reference {reference:?}")))?;
        }
        Ok(value.clone())
    }

    /// Resolve local references into a standalone schema or parameter.
    pub fn resolve(&self, value: &JsonValue) -> Result<JsonValue, KitError> {
        self.resolve_seen(value, &HashSet::new())
    }

    fn resolve_seen(
        &self,
        value: &JsonValue,
        seen: &HashSet<String>,
    ) -> Result<JsonValue, KitError> {
        match value {
            JsonValue::Object(members) => {
                if let Some(reference) = members.get("$ref").and_then(JsonValue::as_str) {
                    if seen.contains(reference) {
                        return Ok(JsonValue::Object(Map::new()));
                    }
                    let mut inner = seen.clone();
                    inner.insert(reference.to_owned());
                    let mut resolved = self.resolve_seen(&self.resolve_ref(reference)?, &inner)?;
                    let additions: Map<String, JsonValue> = members
                        .iter()
                        .filter(|(key, _)| key.as_str() != "$ref")
                        .map(|(key, item)| (key.clone(), item.clone()))
                        .collect();
                    if !additions.is_empty() {
                        let additions = self.resolve_seen(&JsonValue::Object(additions), seen)?;
                        if let (Some(target), JsonValue::Object(extra)) =
                            (resolved.as_object_mut(), additions)
                        {
                            target.extend(extra);
                        }
                    }
                    return Ok(resolved);
                }
                let mut result = Map::new();
                for (key, item) in members {
                    result.insert(key.clone(), self.resolve_seen(item, seen)?);
                }
                Ok(JsonValue::Object(result))
            }
            JsonValue::Array(items) => Ok(JsonValue::Array(
                items
                    .iter()
                    .map(|item| self.resolve_seen(item, seen))
                    .collect::<Result<_, _>>()?,
            )),
            other => Ok(other.clone()),
        }
    }

    fn request_body_source(&self, value: &JsonValue) -> Result<JsonValue, KitError> {
        let mut value = value.clone();
        let mut seen = HashSet::new();
        while let Some(reference) = value
            .get("$ref")
            .and_then(JsonValue::as_str)
            .map(str::to_owned)
        {
            if !seen.insert(reference.clone()) {
                return Ok(JsonValue::Object(Map::new()));
            }
            let mut merged = self.resolve_ref(&reference)?;
            if let (Some(target), Some(source)) = (merged.as_object_mut(), value.as_object()) {
                for (key, item) in source {
                    if key != "$ref" {
                        target.insert(key.clone(), item.clone());
                    }
                }
            }
            value = merged;
        }
        Ok(if value.is_object() {
            value
        } else {
            JsonValue::Object(Map::new())
        })
    }

    fn component_name(schema: Option<&JsonValue>) -> Option<String> {
        let reference = schema?.get("$ref")?.as_str()?;
        let name = reference.strip_prefix("#/components/schemas/")?;
        (!name.is_empty()).then(|| unescape_pointer(name))
    }

    fn shared_xml(schema: &JsonValue) -> Option<JsonValue> {
        if let Some(xml) = schema.get("xml").filter(|xml| xml.is_object()) {
            return Some(xml.clone());
        }
        let candidates = non_empty_array(schema.get("oneOf"))
            .or_else(|| non_empty_array(schema.get("anyOf")))?;
        let values: Vec<&JsonValue> = candidates
            .iter()
            .filter_map(|item| non_empty_object(item.get("xml")))
            .collect();
        let first = values.first()?;
        values
            .iter()
            .all(|value| value == first)
            .then(|| (*first).clone())
    }

    fn load_operations(&self) -> Result<IndexMap<String, Operation>, KitError> {
        let mut operations = IndexMap::new();
        let default_security = self.document.get("security");
        let Some(paths) = self.document.get("paths").and_then(JsonValue::as_object) else {
            return Ok(operations);
        };
        for (path, path_item) in paths {
            let path_parameters: Vec<JsonValue> = path_item
                .get("parameters")
                .and_then(JsonValue::as_array)
                .into_iter()
                .flatten()
                .map(|item| self.resolve(item))
                .collect::<Result<_, _>>()?;
            let Some(methods) = path_item.as_object() else {
                continue;
            };
            for (method, raw) in methods {
                if !matches!(
                    method.to_lowercase().as_str(),
                    "get" | "post" | "put" | "patch" | "delete" | "head"
                ) {
                    continue;
                }
                let Some(operation_id) = raw
                    .get("operationId")
                    .and_then(JsonValue::as_str)
                    .filter(|id| !id.is_empty())
                else {
                    continue;
                };
                let mut parameters = path_parameters.clone();
                for item in raw
                    .get("parameters")
                    .and_then(JsonValue::as_array)
                    .into_iter()
                    .flatten()
                {
                    parameters.push(self.resolve(item)?);
                }
                let security = raw.get("security").or(default_security);
                let protected = security != Some(&JsonValue::Array(Vec::new()));
                let roles = raw
                    .get("x-mosaiq-roles")
                    .and_then(JsonValue::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(|role| role.as_str().map(str::to_owned))
                    .collect();
                let raw_request_body = raw
                    .get("requestBody")
                    .cloned()
                    .unwrap_or_else(|| JsonValue::Object(Map::new()));
                let source = self.request_body_source(&raw_request_body)?;
                let component_name = Self::component_name(
                    media(source.get("content")).and_then(|m| m.get("schema")),
                );
                let request_body = self.resolve(&raw_request_body)?;
                let mut request_schema = None;
                let mut xml_name = None;
                let mut xml_namespace = None;
                if let Some(schema) =
                    media(request_body.get("content")).and_then(|m| m.get("schema"))
                {
                    let schema = self.resolve(schema)?;
                    if let Some(xml) = Self::shared_xml(&schema) {
                        let text = |key: &str| {
                            xml.get(key)
                                .and_then(JsonValue::as_str)
                                .filter(|value| !value.is_empty())
                                .map(str::to_owned)
                        };
                        xml_name = text("name").or_else(|| component_name.clone());
                        xml_namespace = text("namespace").or_else(|| Some(self.namespace.clone()));
                    } else if let Some(name) = &component_name {
                        xml_name = Some(name.clone());
                        xml_namespace = Some(self.namespace.clone());
                    }
                    request_schema = Some(schema);
                }
                let validator = request_schema
                    .as_ref()
                    .map(|schema| {
                        jsonschema::draft202012::options()
                            .should_validate_formats(true)
                            .build(schema)
                            .map(Arc::new)
                            .map_err(|error| {
                                kit_error(format!(
                                    "Operation {operation_id:?} has an invalid request schema: {error}"
                                ))
                            })
                    })
                    .transpose()?;
                operations.insert(
                    operation_id.to_owned(),
                    Operation {
                        operation_id: operation_id.to_owned(),
                        path: path.clone(),
                        method: method.to_uppercase(),
                        protected,
                        roles,
                        parameters,
                        request_schema,
                        xml_name,
                        xml_namespace,
                        validator,
                    },
                );
            }
        }
        Ok(operations)
    }

    pub fn operation(&self, operation_id: &str) -> Result<&Operation, KitError> {
        self.operations
            .get(operation_id)
            .ok_or_else(|| kit_error(format!("Unknown OpenAPI operationId: {operation_id}")))
    }

    /// Parse a JSON body exactly: duplicate members and non-finite constants are refused.
    pub fn parse_json(&self, raw: &[u8]) -> Result<JsonValue, ApiError> {
        let mut checker = serde_json::Deserializer::from_slice(raw);
        DuplicateCheck::deserialize(&mut checker).map_err(|_| ApiError::malformed_body())?;
        checker.end().map_err(|_| ApiError::malformed_body())?;
        serde_json::from_slice(raw).map_err(|_| ApiError::malformed_body())
    }

    /// Parse and validate an XML body against the service XSD.
    pub fn parse_xml(&self, raw: &[u8], operation: &Operation) -> Result<JsonValue, ApiError> {
        if raw.contains(&0) || FORBIDDEN_XML.is_match(raw) {
            return Err(ApiError::malformed_body());
        }
        let root = parse_tree(raw).ok_or_else(ApiError::malformed_body)?;
        if root.namespace.as_deref() != operation.xml_namespace.as_deref()
            || Some(root.name.as_str()) != operation.xml_name.as_deref()
        {
            return Err(ApiError::invalid_body(vec![Detail::new("$", "invalid")]));
        }
        let options = ParserOptions {
            recover: false,
            no_net: true,
            no_def_dtd: true,
            huge: false,
            ..ParserOptions::default()
        };
        let document = Parser::default()
            .parse_string_with_options(raw, options)
            .map_err(|_| ApiError::malformed_body())?;
        let outcome = with_xml_schema(&self.xsd_path, |schema| schema.validate_document(&document))
            .map_err(|error| {
                tracing::error!("XSD unavailable for validation: {error}");
                ApiError::internal()
            })?;
        if let Err(errors) = outcome {
            let mut details = Vec::new();
            for error in errors {
                let detail = xsd_detail(error.message.as_deref().unwrap_or(""));
                if !details.contains(&detail) {
                    details.push(detail);
                }
            }
            if details.is_empty() {
                details.push(Detail::new("$", "invalid"));
            }
            return Err(ApiError::invalid_body(details));
        }
        let schema = operation
            .request_schema
            .clone()
            .unwrap_or_else(|| JsonValue::Object(Map::new()));
        Ok(element_to_value(&root, &schema))
    }

    /// Validate a parsed body against the operation's JSON Schema.
    pub fn validate(&self, value: &JsonValue, operation: &Operation) -> Result<(), ApiError> {
        let Some(validator) = &operation.validator else {
            return Ok(());
        };
        // jsonschema 0.30 panics comparing a number no f64 holds against a
        // bound (#583), so such a number is refused before it reaches it.
        let mut oversized = Vec::new();
        oversized_numbers(value, &mut Vec::new(), &mut oversized);
        if !oversized.is_empty() {
            oversized.sort();
            let details = oversized
                .iter()
                .map(|path| Detail::new(field_name(path), "out_of_range"))
                .collect();
            return Err(ApiError::invalid_body(details));
        }
        let mut errors: Vec<(Vec<String>, Detail)> = validator
            .iter_errors(value)
            .map(|error| {
                let path: Vec<String> = error
                    .instance_path
                    .into_iter()
                    .map(|segment| match segment {
                        LocationSegment::Property(name) => name.to_string(),
                        LocationSegment::Index(index) => index.to_string(),
                    })
                    .collect();
                let mut field = field_name(&path);
                let code = match &error.kind {
                    ValidationErrorKind::Required { property } => {
                        field = property
                            .as_str()
                            .map_or_else(|| property.to_string(), str::to_owned);
                        "required"
                    }
                    ValidationErrorKind::AdditionalProperties { unexpected } => {
                        if let Some(first) = unexpected.first() {
                            field = first.clone();
                        }
                        "unexpected_field"
                    }
                    ValidationErrorKind::Type { .. } => "invalid_type",
                    ValidationErrorKind::MinLength { .. } => "too_short",
                    ValidationErrorKind::MaxLength { .. } => "too_long",
                    ValidationErrorKind::Minimum { .. }
                    | ValidationErrorKind::Maximum { .. }
                    | ValidationErrorKind::ExclusiveMinimum { .. }
                    | ValidationErrorKind::ExclusiveMaximum { .. } => "out_of_range",
                    ValidationErrorKind::Format { .. } | ValidationErrorKind::Pattern { .. } => {
                        "invalid_format"
                    }
                    ValidationErrorKind::Enum { .. } | ValidationErrorKind::Constant { .. } => {
                        "not_allowed"
                    }
                    ValidationErrorKind::UniqueItems => "duplicate",
                    _ => "invalid",
                };
                (path, Detail::new(field, code))
            })
            .collect();
        if errors.is_empty() {
            return Ok(());
        }
        errors.sort_by(|left, right| left.0.cmp(&right.0));
        let mut details = Vec::new();
        for (_, detail) in errors {
            if !details.contains(&detail) {
                details.push(detail);
            }
        }
        Err(ApiError::invalid_body(details))
    }
}

/// A detail's field: the instance path joined with dots, or `$` for the root.
fn field_name(path: &[String]) -> String {
    if path.is_empty() {
        "$".to_owned()
    } else {
        path.join(".")
    }
}

/// Collect the path of every number that is neither a u64, an i64 nor a finite
/// f64, such as `1e400` or a 400-digit integer.
fn oversized_numbers(value: &JsonValue, path: &mut Vec<String>, found: &mut Vec<Vec<String>>) {
    match value {
        JsonValue::Number(number) => {
            if number.as_u64().is_none() && number.as_i64().is_none() && number.as_f64().is_none() {
                found.push(path.clone());
            }
        }
        JsonValue::Array(items) => {
            for (index, item) in items.iter().enumerate() {
                path.push(index.to_string());
                oversized_numbers(item, path, found);
                path.pop();
            }
        }
        JsonValue::Object(members) => {
            for (name, member) in members {
                path.push(name.clone());
                oversized_numbers(member, path, found);
                path.pop();
            }
        }
        _ => {}
    }
}

fn non_empty_array(value: Option<&JsonValue>) -> Option<&Vec<JsonValue>> {
    value
        .and_then(JsonValue::as_array)
        .filter(|items| !items.is_empty())
}

fn target_namespace(xsd: &[u8]) -> Option<String> {
    let mut reader = NsReader::from_reader(xsd);
    let mut buffer = Vec::new();
    loop {
        match reader.read_resolved_event_into(&mut buffer).ok()? {
            (_, Event::Start(element)) | (_, Event::Empty(element)) => {
                let attribute = element
                    .try_get_attribute("targetNamespace")
                    .ok()
                    .flatten()?;
                let value = attribute.unescape_value().ok()?.into_owned();
                return (!value.is_empty()).then_some(value);
            }
            (_, Event::Eof) => return None,
            _ => {}
        }
        buffer.clear();
    }
}

/// Map one libxml2 schema-validation message to a stable detail.
pub fn xsd_detail(message: &str) -> Detail {
    let mut field = XSD_ELEMENT
        .captures(message)
        .and_then(|captures| captures.get(1))
        .map_or("$", |found| found.as_str())
        .to_owned();
    let has = |needle: &str| message.contains(needle);
    let code = if has("Missing child element") {
        if let Some(missing) = XSD_MISSING.captures(message).and_then(|c| c.get(1)) {
            field = missing.as_str().to_owned();
        }
        "required"
    } else if has("This element is not expected") {
        "unexpected_field"
    } else if has("[facet 'pattern']") {
        "invalid_format"
    } else if has("[facet 'minLength']") {
        "too_short"
    } else if has("[facet 'maxLength']") {
        "too_long"
    } else if has("[facet 'minInclusive']")
        || has("[facet 'maxInclusive']")
        || has("[facet 'minExclusive']")
        || has("[facet 'maxExclusive']")
    {
        "out_of_range"
    } else if has("[facet 'enumeration']") {
        "not_allowed"
    } else if has("not a valid value of the atomic type") {
        "invalid_type"
    } else {
        "invalid"
    };
    Detail::new(field, code)
}

/// Walks a JSON document and fails on a duplicate object member.
struct DuplicateCheck;

impl<'de> Deserialize<'de> for DuplicateCheck {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(DuplicateCheckVisitor)
    }
}

struct DuplicateCheckVisitor;

impl<'de> Visitor<'de> for DuplicateCheckVisitor {
    type Value = DuplicateCheck;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value")
    }

    fn visit_bool<E>(self, _value: bool) -> Result<Self::Value, E> {
        Ok(DuplicateCheck)
    }

    fn visit_i64<E>(self, _value: i64) -> Result<Self::Value, E> {
        Ok(DuplicateCheck)
    }

    fn visit_u64<E>(self, _value: u64) -> Result<Self::Value, E> {
        Ok(DuplicateCheck)
    }

    fn visit_f64<E>(self, _value: f64) -> Result<Self::Value, E> {
        Ok(DuplicateCheck)
    }

    fn visit_str<E>(self, _value: &str) -> Result<Self::Value, E> {
        Ok(DuplicateCheck)
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(DuplicateCheck)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut items: A) -> Result<Self::Value, A::Error> {
        while items.next_element::<DuplicateCheck>()?.is_some() {}
        Ok(DuplicateCheck)
    }

    fn visit_map<A: MapAccess<'de>>(self, mut members: A) -> Result<Self::Value, A::Error> {
        let mut keys = HashSet::new();
        while let Some(key) = members.next_key::<String>()? {
            if !keys.insert(key) {
                return Err(de::Error::custom("duplicate member"));
            }
            members.next_value::<DuplicateCheck>()?;
        }
        Ok(DuplicateCheck)
    }
}

/// A namespace-resolved element tree read by quick-xml.
#[derive(Debug, Default)]
struct Element {
    namespace: Option<String>,
    name: String,
    children: Vec<Element>,
    text: String,
    /// `xsi:nil="true"` (or `1`): the XML spelling of JSON null.
    nil: bool,
}

/// Read the document with quick-xml. A DOCTYPE or an entity other than the
/// five predefined ones and character references is refused, so nothing is
/// ever expanded or fetched.
fn parse_tree(raw: &[u8]) -> Option<Element> {
    let mut reader = NsReader::from_reader(raw);
    reader.config_mut().check_end_names = true;
    let mut buffer = Vec::new();
    let mut stack: Vec<Element> = Vec::new();
    let mut root = None;
    loop {
        let (resolved, event) = reader.read_resolved_event_into(&mut buffer).ok()?;
        let namespace = match resolved {
            ResolveResult::Bound(namespace) => {
                Some(String::from_utf8(namespace.as_ref().to_vec()).ok()?)
            }
            ResolveResult::Unbound => None,
            ResolveResult::Unknown(_) => return None,
        };
        let is_empty = matches!(event, Event::Empty(_));
        match event {
            Event::Start(start) | Event::Empty(start) => {
                if root.is_some() {
                    return None;
                }
                let name = std::str::from_utf8(start.local_name().as_ref())
                    .ok()?
                    .to_owned();
                let nil = is_nil(&reader, &start);
                let element = Element {
                    namespace,
                    name,
                    nil,
                    ..Element::default()
                };
                if is_empty {
                    match stack.last_mut() {
                        Some(parent) => parent.children.push(element),
                        None => root = Some(element),
                    }
                } else {
                    stack.push(element);
                }
            }
            Event::End(_) => {
                let element = stack.pop()?;
                match stack.last_mut() {
                    Some(parent) => parent.children.push(element),
                    None => root = Some(element),
                }
            }
            Event::Text(text) => {
                let text = text.decode().ok()?;
                match stack.last_mut() {
                    Some(element) => element.text.push_str(&text),
                    None if text.trim().is_empty() => {}
                    None => return None,
                }
            }
            Event::CData(data) => {
                let text = data.decode().ok()?;
                stack.last_mut()?.text.push_str(&text);
            }
            Event::GeneralRef(reference) => {
                let element = stack.last_mut()?;
                if let Some(character) = reference.resolve_char_ref().ok()? {
                    element.text.push(character);
                } else {
                    let name = reference.decode().ok()?;
                    let character = match name.as_ref() {
                        "lt" => '<',
                        "gt" => '>',
                        "amp" => '&',
                        "apos" => '\'',
                        "quot" => '"',
                        _ => return None,
                    };
                    debug_assert!(PREDEFINED_ENTITIES.contains(&name.as_ref()));
                    element.text.push(character);
                }
            }
            Event::DocType(_) => return None,
            Event::Decl(_) | Event::PI(_) | Event::Comment(_) => {}
            Event::Eof => break,
        }
        buffer.clear();
    }
    if !stack.is_empty() {
        return None;
    }
    root
}

const XSI: &[u8] = b"http://www.w3.org/2001/XMLSchema-instance";

fn is_nil(reader: &NsReader<&[u8]>, start: &quick_xml::events::BytesStart) -> bool {
    start.attributes().flatten().any(|attribute| {
        let (resolved, local) = reader.resolve_attribute(attribute.key);
        matches!(resolved, ResolveResult::Bound(namespace) if namespace.as_ref() == XSI)
            && local.as_ref() == b"nil"
            && matches!(attribute.value.as_ref(), b"true" | b"1")
    })
}

fn nullable(schema: &JsonValue) -> bool {
    match schema.get("type") {
        Some(JsonValue::String(kind)) if kind == "null" => return true,
        Some(JsonValue::Array(kinds)) if kinds.iter().any(|kind| kind == "null") => return true,
        _ => {}
    }
    ["oneOf", "anyOf"].iter().any(|keyword| {
        schema
            .get(keyword)
            .and_then(JsonValue::as_array)
            .is_some_and(|branches| branches.iter().any(nullable))
    })
}

fn required_names(schema: &JsonValue, names: &mut Vec<String>) {
    if let Some(required) = schema.get("required").and_then(JsonValue::as_array) {
        names.extend(
            required
                .iter()
                .filter_map(JsonValue::as_str)
                .map(str::to_owned),
        );
    }
    for branch in schema
        .get("allOf")
        .and_then(JsonValue::as_array)
        .into_iter()
        .flatten()
    {
        required_names(branch, names);
    }
}

fn schema_type(schema: &JsonValue) -> Option<String> {
    match schema.get("type") {
        Some(JsonValue::Array(types)) => {
            return types
                .iter()
                .filter_map(JsonValue::as_str)
                .find(|item| *item != "null")
                .map(str::to_owned);
        }
        Some(JsonValue::String(kind)) if !kind.is_empty() => return Some(kind.clone()),
        _ => {}
    }
    for keyword in ["oneOf", "anyOf"] {
        for branch in schema
            .get(keyword)
            .and_then(JsonValue::as_array)
            .into_iter()
            .flatten()
        {
            if let Some(kind) = schema_type(branch).filter(|kind| kind != "null") {
                return Some(kind);
            }
        }
    }
    None
}

fn merged_properties(schema: &JsonValue) -> Map<String, JsonValue> {
    let mut properties = schema
        .get("properties")
        .and_then(JsonValue::as_object)
        .cloned()
        .unwrap_or_default();
    for keyword in ["allOf", "oneOf", "anyOf"] {
        for branch in schema
            .get(keyword)
            .and_then(JsonValue::as_array)
            .into_iter()
            .flatten()
        {
            properties.extend(merged_properties(branch));
        }
    }
    properties
}

/// Parse a number exactly, as Python's `Decimal` or `int` would accept it.
fn exact_number(text: &str, integer: bool) -> Option<JsonValue> {
    let trimmed = text.trim();
    let (sign, digits) = match trimmed.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", trimmed.strip_prefix('+').unwrap_or(trimmed)),
    };
    if digits.is_empty() || digits.starts_with(['+', '-']) {
        return None;
    }
    let mut normalized = String::from(sign);
    if integer {
        if !digits.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        let digits = digits.trim_start_matches('0');
        normalized.push_str(if digits.is_empty() { "0" } else { digits });
    } else {
        if digits.starts_with('.') {
            normalized.push('0');
        }
        normalized.push_str(digits);
        if normalized.ends_with('.') {
            normalized.push('0');
        }
    }
    serde_json::from_str::<serde_json::Number>(&normalized)
        .ok()
        .map(JsonValue::Number)
}

fn element_to_value(element: &Element, schema: &JsonValue) -> JsonValue {
    if element.nil {
        return JsonValue::Null;
    }
    let kind = schema_type(schema);
    if kind.as_deref() == Some("object") || !element.children.is_empty() {
        let properties = merged_properties(schema);
        let mut grouped: IndexMap<&str, Vec<&Element>> = IndexMap::new();
        for child in &element.children {
            grouped.entry(child.name.as_str()).or_default().push(child);
        }
        let empty = JsonValue::Object(Map::new());
        let mut result = Map::new();
        for (name, children) in grouped {
            let child_schema = properties.get(name).unwrap_or(&empty);
            let value = if schema_type(child_schema).as_deref() == Some("array") {
                let item_schema = child_schema.get("items").unwrap_or(&empty);
                JsonValue::Array(
                    children
                        .iter()
                        .map(|child| element_to_value(child, item_schema))
                        .collect(),
                )
            } else {
                element_to_value(children[0], child_schema)
            };
            result.insert(name.to_owned(), value);
        }
        // "An absent value is omitted in XML and is null in JSON": a member that
        // is required and nullable, and omitted, reads as null.
        let mut required = Vec::new();
        required_names(schema, &mut required);
        for name in required {
            if !result.contains_key(&name) && properties.get(&name).is_some_and(nullable) {
                result.insert(name, JsonValue::Null);
            }
        }
        return JsonValue::Object(result);
    }
    let text = element.text.as_str();
    let as_text = || JsonValue::String(text.to_owned());
    match kind.as_deref() {
        Some("integer") => exact_number(text, true).unwrap_or_else(as_text),
        Some("number") => exact_number(text, false).unwrap_or_else(as_text),
        Some("boolean") => match text {
            "true" | "1" => JsonValue::Bool(true),
            "false" | "0" => JsonValue::Bool(false),
            _ => as_text(),
        },
        _ => as_text(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn libxml2_messages_map_to_stable_details() {
        let cases = [
            (
                "Element '{urn:x}probe': Missing child element(s). Expected is ( {urn:x}title ).\n",
                ("title", "required"),
            ),
            (
                "Element '{urn:x}extra': This element is not expected. Expected is ( {urn:x}code ).\n",
                ("extra", "unexpected_field"),
            ),
            (
                "Element '{urn:x}level': 'no' is not a valid value of the atomic type 'xs:integer'.\n",
                ("level", "invalid_type"),
            ),
            (
                "Element '{urn:x}color': [facet 'enumeration'] The value 'blue' is not an element of the set {'red', 'green'}.\n",
                ("color", "not_allowed"),
            ),
        ];
        for (message, (field, code)) in cases {
            assert_eq!(xsd_detail(message), Detail::new(field, code), "{message}");
        }
    }

    #[test]
    fn quick_xml_refuses_doctype_and_unknown_entities() {
        assert!(parse_tree(b"<a>&lt;&#65;</a>").is_some_and(|root| root.text == "<A"));
        assert!(parse_tree(b"<a>&custom;</a>").is_none());
        assert!(parse_tree(b"<!DOCTYPE a><a/>").is_none());
        assert!(parse_tree(b"<a><b></a>").is_none());
        assert!(parse_tree(b"<a/><b/>").is_none());
    }

    fn read_xml(body: &str, schema: &JsonValue) -> JsonValue {
        let raw = format!("<R xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\">{body}</R>");
        element_to_value(&parse_tree(raw.as_bytes()).expect("well formed"), schema)
    }

    fn nil_schema() -> JsonValue {
        serde_json::json!({
            "type": "object",
            "required": ["name", "email", "phone", "note"],
            "properties": {
                "name": {"type": "string"},
                "email": {"type": ["string", "null"]},
                "phone": {"oneOf": [{"type": "string"}, {"type": "null"}]},
                "note": {"type": "string"},
                "parent": {"type": ["integer", "null"]}
            }
        })
    }

    #[test]
    fn omitted_required_nullable_members_read_as_null() {
        let value = read_xml("<name>A</name>", &nil_schema());
        // `note` is required but not nullable: it stays absent for validation.
        assert_eq!(
            value,
            serde_json::json!({"name": "A", "email": null, "phone": null})
        );
    }

    #[test]
    fn xsi_nil_reads_as_null() {
        let value = read_xml(
            "<name>A</name><email xsi:nil=\"true\"/><parent xsi:nil=\"1\"></parent>\
             <phone>1</phone><note xsi:nil=\"false\">x</note>",
            &nil_schema(),
        );
        assert_eq!(value["email"], JsonValue::Null);
        assert_eq!(value["parent"], JsonValue::Null);
        assert_eq!(value["phone"], "1");
        assert_eq!(value["note"], "x");
    }

    #[test]
    fn numbers_from_xml_are_exact() {
        assert_eq!(
            exact_number("0.10000000000000000001", false).map(|n| n.to_string()),
            Some("0.10000000000000000001".to_owned())
        );
        assert_eq!(
            exact_number("+5", true).map(|n| n.to_string()),
            Some("5".to_owned())
        );
        assert!(exact_number("1.5", true).is_none());
    }

    #[test]
    fn numbers_no_f64_holds_are_found_by_path() {
        let huge_integer = format!("1{}", "0".repeat(399));
        let raw = format!(
            r#"{{"a":1e400,"b":[1,-1e400,1e308,18446744073709551616],"c":{{"d":1e309,"e":{huge_integer}}},"f":-9223372036854775808,"g":1e-400}}"#
        );
        let value: JsonValue = serde_json::from_str(&raw).unwrap();
        let mut found = Vec::new();
        oversized_numbers(&value, &mut Vec::new(), &mut found);
        let fields: Vec<String> = found.iter().map(|path| field_name(path)).collect();
        assert_eq!(fields, ["a", "b.1", "c.d", "c.e"]);
    }
}
