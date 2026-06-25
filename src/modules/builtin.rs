use super::{ModuleManifest, ModuleToolSpec};
use serde_json::json;

pub fn all_modules() -> Vec<ModuleManifest> {
    vec![ocr(), pdf()]
}

fn ocr() -> ModuleManifest {
    ModuleManifest {
        name: "ocr".into(),
        description: "Extract text from images using Tesseract OCR".into(),
        version: "1.0",
        install_linux: vec!["sudo apt-get install -y tesseract-ocr".into()],
        install_macos: vec!["brew install tesseract".into()],
        install_windows: vec!["choco install tesseract -y".into()],
        tools: vec![ModuleToolSpec {
            name: "ocr_image".into(),
            description: "Extract text from an image file using OCR.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the image file"
                    },
                    "language": {
                        "type": "string",
                        "description": "Tesseract language code: eng (English), ita (Italian), deu (German), fra (French). Use eng if unsure."
                    }
                },
                "required": ["image_path", "language"]
            }),
            command: "tesseract {image_path} stdout -l {language}".into(),
        }],
    }
}

fn pdf() -> ModuleManifest {
    ModuleManifest {
        name: "pdf".into(),
        description: "Extract text from PDF files using poppler-utils".into(),
        version: "1.0",
        install_linux: vec!["sudo apt-get install -y poppler-utils".into()],
        install_macos: vec!["brew install poppler".into()],
        install_windows: vec!["choco install poppler -y".into()],
        tools: vec![ModuleToolSpec {
            name: "pdf_to_text".into(),
            description: "Extract all text content from a PDF file".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the PDF file"
                    }
                },
                "required": ["pdf_path"]
            }),
            command: "pdftotext {pdf_path} -".into(),
        }],
    }
}
