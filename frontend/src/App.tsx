import { useEffect, useRef, useState } from "react";
import "./App.css";

const API = "http://127.0.0.1:8000/api/uploads/";

const CATEGORIES = {
  video: {
    label: "Lecture Video",
    extensions: [".mp4", ".mkv", ".mov"],
  },
  textbook: {
    label: "Textbook",
    extensions: [".pdf"],
  },
  slides: {
    label: "Slides",
    extensions: [".ppt", ".pptx", ".pdf"],
  },
  document: {
    label: "PDF / Document",
    extensions: [".pdf"],
  },
} as const;

type Category = keyof typeof CATEGORIES;

type UploadedFile = {
  id: number;
  original_filename: string;
  file_type: Category;
  mime_type: string | null;
  file_size: number;
  status: string;
  uploaded_at: string;
};

type UploadResponse = {
  id: number;
  filename: string;
  file_type: Category;
  status: string;
};

const MAX_MB = 500;

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function App() {
  const [category, setCategory] = useState<Category>("video");
  const [file, setFile] = useState<File | null>(null);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"success" | "error" | null>(
    null,
  );
  const [dragOver, setDragOver] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);

  async function loadFiles() {
    try {
      const response = await fetch(API);

      if (!response.ok) {
        throw new Error("Could not load uploaded files");
      }

      const data: UploadedFile[] = await response.json();
      setFiles(data);
    } catch {
      setMessage("Could not load uploaded files. Is the backend running?");
      setMessageType("error");
    }
  }

  useEffect(() => {
    loadFiles();
  }, []);

  function chooseFile(selectedFile: File | undefined) {
    if (!selectedFile) {
      return;
    }

    setMessage(null);
    setMessageType(null);
    setProgress(0);

    const extension = `.${selectedFile.name
      .split(".")
      .pop()
      ?.toLowerCase()}`;

    if (!CATEGORIES[category].extensions.includes(extension as never)) {
      setFile(null);
      setMessage(
        `${extension} is not allowed for ${CATEGORIES[category].label}. ` +
          `Allowed: ${CATEGORIES[category].extensions.join(", ")}`,
      );
      setMessageType("error");
      return;
    }

    if (selectedFile.size > MAX_MB * 1024 * 1024) {
      setFile(null);
      setMessage(`File is larger than ${MAX_MB} MB.`);
      setMessageType("error");
      return;
    }

    setFile(selectedFile);
  }

  function changeCategory(event: React.ChangeEvent<HTMLSelectElement>) {
    const newCategory = event.target.value as Category;

    setCategory(newCategory);
    setFile(null);
    setMessage(null);
    setMessageType(null);
    setProgress(0);

    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }

  async function uploadFile() {
    if (!file) {
      return;
    }

    const formData = new FormData();

    formData.append("content_type", category);
    formData.append("file", file);

    setUploading(true);
    setProgress(0);
    setMessage(null);
    setMessageType(null);

    try {
      const xhr = new XMLHttpRequest();

      const uploadPromise = new Promise<UploadResponse>((resolve, reject) => {
        xhr.open("POST", API);

        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            setProgress(Math.round((event.loaded / event.total) * 100));
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(JSON.parse(xhr.responseText));
          } else {
            try {
              const error = JSON.parse(xhr.responseText);
              reject(new Error(error.detail || "Upload failed."));
            } catch {
              reject(new Error("Upload failed."));
            }
          }
        };

        xhr.onerror = () => {
          reject(new Error("Could not connect to the backend."));
        };

        xhr.send(formData);
      });

      const result = await uploadPromise;

      setProgress(100);
      setMessage("Upload successful!");
      setMessageType("success");

      console.log("Upload response:", result);

      setFile(null);

      if (inputRef.current) {
        inputRef.current.value = "";
      }

      await loadFiles();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Upload failed.",
      );
      setMessageType("error");
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className="container">
      <header className="header">
        <h1>Adaptive AI Tutor</h1>
        <p>Upload your learning materials</p>
      </header>

      <section className="card">
        <h2>Upload Learning Material</h2>

        <label htmlFor="content-type">Content Type</label>

        <select
          id="content-type"
          value={category}
          onChange={changeCategory}
          disabled={uploading}
        >
          {Object.entries(CATEGORIES).map(([key, value]) => (
            <option key={key} value={key}>
              {value.label}
            </option>
          ))}
        </select>

        <div
          className={`dropzone ${dragOver ? "drag-over" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => {
            setDragOver(false);
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            chooseFile(event.dataTransfer.files[0]);
          }}
        >
          <strong>Choose a file or drag it here</strong>

          <span>
            Allowed: {CATEGORIES[category].extensions.join(", ")}
          </span>

          <span>Maximum size: {MAX_MB} MB</span>

          <input
            ref={inputRef}
            type="file"
            hidden
            accept={CATEGORIES[category].extensions.join(",")}
            onChange={(event) => chooseFile(event.target.files[0])}
          />
        </div>

        {file && (
          <div className="file-info">
            <p>
              <strong>File:</strong> {file.name}
            </p>

            <p>
              <strong>Size:</strong> {formatSize(file.size)}
            </p>

            <p>
              <strong>Type:</strong> {CATEGORIES[category].label}
            </p>
          </div>
        )}

        <button
          onClick={uploadFile}
          disabled={!file || uploading}
        >
          {uploading ? "Uploading..." : "Upload"}
        </button>

        {uploading && (
          <div className="progress-container">
            <div className="progress-track">
              <div
                className="progress-bar"
                style={{ width: `${progress}%` }}
              />
            </div>

            <span>{progress}%</span>
          </div>
        )}

        {message && (
          <div className={`message ${messageType}`}>
            {message}
          </div>
        )}
      </section>

      <section className="card">
        <div className="section-header">
          <h2>Uploaded Materials</h2>

          <button
            className="refresh-button"
            onClick={loadFiles}
            disabled={uploading}
          >
            Refresh
          </button>
        </div>

        {files.length === 0 ? (
          <p className="empty">No materials uploaded yet.</p>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th>Type</th>
                  <th>Size</th>
                  <th>Status</th>
                </tr>
              </thead>

              <tbody>
                {files.map((uploadedFile) => (
                  <tr key={uploadedFile.id}>
                    <td>{uploadedFile.original_filename}</td>

                    <td>
                      {CATEGORIES[uploadedFile.file_type]?.label ??
                        uploadedFile.file_type}
                    </td>

                    <td>{formatSize(uploadedFile.file_size)}</td>

                    <td>
                      <span className="status">
                        {uploadedFile.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

export default App;
