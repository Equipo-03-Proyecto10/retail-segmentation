//! A staged contract and an app over it, as kit-py's tests build theirs.

#![allow(dead_code)]

use std::path::{Path, PathBuf};

use axum::Router;
use axum::body::Body;
use http::{Method, Request, Response};
use http_body_util::BodyExt;
use mosaiq_kit::{
    ApiError, Context, Kit, Map, Operation, Page, Reply, Representation, Value, money,
};
use tower::ServiceExt;

pub const NAMESPACE: &str = "https://mosaiq.maxthecoder.online/xml/kit-example/v1";

pub fn repository() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// `contracts/kit-example` and `contracts/common/v1` in a temporary
/// directory, the layout a service image stages.
pub fn staged_contracts() -> tempfile::TempDir {
    let target = tempfile::tempdir().expect("temporary directory");
    let copy = |from: PathBuf, to: PathBuf| {
        std::fs::create_dir_all(&to).unwrap();
        for entry in std::fs::read_dir(from).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_file() {
                std::fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
            }
        }
    };
    copy(
        repository().join("packages/kit-py/example/contracts/kit-example"),
        target.path().join("kit-example"),
    );
    copy(
        repository().join("contracts/common/v1"),
        target.path().join("common/v1"),
    );
    target
}

pub fn operation(id: &str, method: Method, path: &str) -> Operation {
    Operation {
        id: id.into(),
        method,
        path: path.into(),
        protected: false,
        roles: Vec::new(),
        accepts_body: false,
        paginated: false,
    }
}

async fn list_notes(context: Context) -> Result<Reply, ApiError> {
    let mut note = Map::new();
    note.insert("note_id".into(), Value::Int(1));
    note.insert("title".into(), "Note 1".into());
    note.insert("amount".into(), money(rust_decimal::Decimal::new(1, 2)));
    Ok(Page::new("Notes", "Note", "notes", vec![note], context.page, 1).into())
}

async fn create_note(_context: Context) -> Result<Reply, ApiError> {
    let mut data = Map::new();
    data.insert("note_id".into(), Value::Int(46));
    let mut created = Representation::new("Note", data);
    created.status = 201;
    Ok(created.into())
}

async fn get_note(context: Context) -> Result<Reply, ApiError> {
    match context.path.get("note_id").map(String::as_str) {
        Some("1") => {
            let mut data = Map::new();
            data.insert("note_id".into(), Value::Int(1));
            Ok(Representation::new("Note", data).into())
        }
        Some("13") => panic!("password=trace-value"),
        _ => Err(ApiError::new(
            404,
            "note_not_found",
            "The note does not exist.",
        )),
    }
}

pub struct App {
    pub router: Router,
    pub contracts: tempfile::TempDir,
}

pub fn app_with(ready: bool) -> App {
    let contracts = staged_contracts();
    let kit = Kit::builder("kit-example", "kit_test")
        .contracts_dir(contracts.path())
        .ready_check(move || async move { ready })
        .build()
        .expect("the example contract loads");
    let mut list = operation("listNotes", Method::GET, "/v1/notes");
    list.paginated = true;
    let mut create = operation("createNote", Method::POST, "/v1/notes");
    create.accepts_body = true;
    let router = kit
        .routes()
        .route_operation(list, list_notes)
        .route_operation(create, create_note)
        .route_operation(
            operation("getNote", Method::GET, "/v1/notes/{note_id}"),
            get_note,
        )
        .into_router();
    App { router, contracts }
}

pub fn app() -> App {
    app_with(true)
}

pub struct Answer {
    pub status: u16,
    pub headers: http::HeaderMap,
    pub body: Vec<u8>,
}

impl Answer {
    pub fn json(&self) -> serde_json::Value {
        serde_json::from_slice(&self.body).expect("a JSON body")
    }

    pub fn text(&self) -> String {
        String::from_utf8(self.body.clone()).expect("UTF-8")
    }

    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.get(name).and_then(|value| value.to_str().ok())
    }

    pub fn content_type(&self) -> Option<&str> {
        self.header("content-type")
    }
}

pub async fn send(router: &Router, request: Request<Body>) -> Answer {
    let response: Response<Body> = router.clone().oneshot(request).await.unwrap();
    let status = response.status().as_u16();
    let headers = response.headers().clone();
    let body = response
        .into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec();
    Answer {
        status,
        headers,
        body,
    }
}

pub fn get(path: &str, headers: &[(&str, &str)]) -> Request<Body> {
    let mut builder = Request::get(path);
    for (name, value) in headers {
        builder = builder.header(*name, *value);
    }
    builder.body(Body::empty()).unwrap()
}

pub fn post(path: &str, headers: &[(&str, &str)], body: Vec<u8>) -> Request<Body> {
    let mut builder = Request::post(path);
    for (name, value) in headers {
        builder = builder.header(*name, *value);
    }
    builder.body(Body::from(body)).unwrap()
}
