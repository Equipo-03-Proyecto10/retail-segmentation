//! The service's staged contract, as kit-py's `Contract` reads it:
//!
//! ```text
//! contracts/
//!   <service>/openapi.yaml
//!   <service>/<service>.xsd
//!   <service>/<service>.v<N>.xsd   (optional, frozen versions)
//!   common/v1/common.xsd
//! ```

use std::path::{Path, PathBuf};

use bytes::Bytes;
use quick_xml::Reader;
use quick_xml::events::Event;

/// Why a contract could not be loaded; a service refuses to start on it.
#[derive(Debug)]
pub struct ContractError(pub String);

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ContractError {}

pub(crate) struct Contract {
    pub(crate) root: PathBuf,
    pub(crate) openapi: Bytes,
    pub(crate) xsd: Bytes,
    /// `schema.v<N>.xsd` route names and their bytes.
    pub(crate) versioned_xsd: Vec<(String, Bytes)>,
    /// The service XSD's `targetNamespace`, the default namespace of XML
    /// responses.
    pub(crate) namespace: String,
}

fn read(path: &Path) -> Result<Bytes, ContractError> {
    std::fs::read(path)
        .map(Bytes::from)
        .map_err(|error| ContractError(format!("cannot read {}: {error}", path.display())))
}

fn target_namespace(xsd: &[u8], path: &Path) -> Result<String, ContractError> {
    let mut reader = Reader::from_reader(xsd);
    let mut buffer = Vec::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(element) | Event::Empty(element)) => {
                for attribute in element.attributes().flatten() {
                    if attribute.key.as_ref() == b"targetNamespace" {
                        return attribute
                            .unescape_value()
                            .map(|value| value.into_owned())
                            .map_err(|error| {
                                ContractError(format!("{}: {error}", path.display()))
                            });
                    }
                }
                break;
            }
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(error) => return Err(ContractError(format!("{}: {error}", path.display()))),
        }
        buffer.clear();
    }
    Err(ContractError(format!(
        "{} has no targetNamespace",
        path.display()
    )))
}

impl Contract {
    pub(crate) fn load(contracts_dir: &Path, service: &str) -> Result<Self, ContractError> {
        let directory = contracts_dir.join(service);
        let openapi = read(&directory.join("openapi.yaml"))?;
        let xsd_path = directory.join(format!("{service}.xsd"));
        let xsd = read(&xsd_path)?;
        let namespace = target_namespace(&xsd, &xsd_path)?;
        let prefix = format!("{service}.v");
        let mut versioned_xsd = Vec::new();
        if let Ok(entries) = std::fs::read_dir(&directory) {
            for entry in entries.flatten() {
                let file = entry.file_name().to_string_lossy().into_owned();
                if let Some(version) = file
                    .strip_prefix(&prefix)
                    .and_then(|rest| rest.strip_suffix(".xsd"))
                {
                    versioned_xsd.push((format!("v{version}"), read(&entry.path())?));
                }
            }
        }
        versioned_xsd.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(Self {
            root: contracts_dir.to_path_buf(),
            openapi,
            xsd,
            versioned_xsd,
            namespace,
        })
    }
}
