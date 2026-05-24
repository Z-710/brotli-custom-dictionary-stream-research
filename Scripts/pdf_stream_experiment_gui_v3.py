#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "PDF Stream Brotli Experiment GUI v3"


@dataclass
class AppConfig:
    python_exe: str = sys.executable
    scanner_script: str = "pdf_stream_scanner_v2.py"
    extractor_script: str = "pdf_stream_extractor_v2.py"
    dict_builder_script: str = "build_custom_brotli_dict.py"
    benchmark_script: str = "brotli_csv_benchmark.py"
    compare_script: str = "compare_stream_sizes.py"
    brotli_exe: str = "brotli"
    input_dir: str = ""
    work_dir: str = "./pdf_stream_experiment"
    scan_csv: str = "./pdf_stream_experiment/stream_inventory_v2.csv"
    extract_dir: str = "./pdf_stream_experiment/extracted"
    dict_dir: str = "./pdf_stream_experiment/dicts"
    results_dir: str = "./pdf_stream_experiment/results"
    qualities: str = "5,11"
    trials: str = "3"
    train_pct: str = "70"
    seed: str = "42"
    dict_sizes: str = "65536"
    category: str = "content_streams"
    include_content_streams: bool = True
    include_object_streams: bool = True
    include_xref_streams: bool = True
    include_font_streams: bool = True
    include_icc_profiles: bool = False
    include_tagged_structures: bool = False


class Worker(threading.Thread):
    def __init__(self, fn):
        super().__init__(daemon=True)
        self.fn = fn

    def run(self):
        self.fn()


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1280x920")
        self.cfg = AppConfig()
        self.vars: dict[str, tk.Variable] = {}
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.is_busy = False
        self.current_task_name = ""
        self.action_buttons: list[ttk.Button] = []
        self.task_started_at = 0.0
        self.task_heartbeat_stop: threading.Event | None = None
        self._build_ui()
        self._poll()

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        config_tab = ttk.Frame(notebook)
        pipeline_tab = ttk.Frame(notebook)
        notes_tab = ttk.Frame(notebook)
        notebook.add(config_tab, text="Configuration")
        notebook.add(pipeline_tab, text="Pipeline")
        notebook.add(notes_tab, text="Notes")

        self._build_config_tab(config_tab)
        self._build_pipeline_tab(pipeline_tab)
        self._build_notes_tab(notes_tab)

    def _build_config_tab(self, parent: ttk.Frame) -> None:
        left = ttk.Frame(parent)
        right = ttk.Frame(parent)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        paths = ttk.LabelFrame(left, text="Paths and scripts")
        paths.pack(fill="x", pady=6)
        self._path_row(paths, "Python", "python_exe", 0, file_pick=True)
        self._path_row(paths, "Scanner script", "scanner_script", 1, file_pick=True)
        self._path_row(paths, "Extractor script", "extractor_script", 2, file_pick=True)
        self._path_row(paths, "Dictionary builder", "dict_builder_script", 3, file_pick=True)
        self._path_row(paths, "Benchmark script", "benchmark_script", 4, file_pick=True)
        self._path_row(paths, "Compare script", "compare_script", 5, file_pick=True)
        self._path_row(paths, "Brotli executable", "brotli_exe", 6, file_pick=True)
        self._path_row(paths, "Input PDF dir", "input_dir", 7, dir_pick=True)
        self._path_row(paths, "Work dir", "work_dir", 8, dir_pick=True)
        self._path_row(paths, "Scan CSV", "scan_csv", 9, save_file=True)
        self._path_row(paths, "Extract dir", "extract_dir", 10, dir_pick=True)
        self._path_row(paths, "Dictionary dir", "dict_dir", 11, dir_pick=True)
        self._path_row(paths, "Results dir", "results_dir", 12, dir_pick=True)

        opts = ttk.LabelFrame(right, text="Experiment settings")
        opts.pack(fill="x", pady=6)
        self._entry_row(opts, "Qualities", "qualities", 0)
        self._entry_row(opts, "Trials", "trials", 1)
        self._entry_row(opts, "Train %", "train_pct", 2)
        self._entry_row(opts, "Seed", "seed", 3)
        self._entry_row(opts, "Dictionary sizes", "dict_sizes", 4)
        self._entry_row(opts, "Active category", "category", 5)
        self._check_row(opts, "Include content streams", "include_content_streams", 6)
        self._check_row(opts, "Include object streams", "include_object_streams", 7)
        self._check_row(opts, "Include xref streams", "include_xref_streams", 8)
        self._check_row(opts, "Include font streams", "include_font_streams", 9)
        self._check_row(opts, "Include ICC profiles", "include_icc_profiles", 10)
        self._check_row(opts, "Include tagged structures", "include_tagged_structures", 11)

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=8)
        self._add_action_button(actions, "Populate work-dir defaults", self.populate_defaults).pack(side="left", padx=4)
        self._add_action_button(actions, "Save config", self.save_config).pack(side="left", padx=4)
        self._add_action_button(actions, "Load config", self.load_config).pack(side="left", padx=4)
        self._add_action_button(actions, "Open work dir", self.open_work_dir).pack(side="left", padx=4)

    def _build_pipeline_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", padx=8, pady=8)
        self._add_action_button(toolbar, "Scan PDFs", self.run_scan).pack(side="left", padx=4)
        self._add_action_button(toolbar, "Extract streams", self.run_extract).pack(side="left", padx=4)
        self._add_action_button(toolbar, "Build dictionary for active category", self.run_build_dict).pack(side="left", padx=4)
        self._add_action_button(toolbar, "Benchmark vanilla for active category", self.run_benchmark_vanilla).pack(side="left", padx=4)
        self._add_action_button(toolbar, "Benchmark dictionary for active category", self.run_benchmark_dict).pack(side="left", padx=4)
        self._add_action_button(toolbar, "Compare sizes for active category", self.run_compare).pack(side="left", padx=4)
        self._add_action_button(toolbar, "Run active category pipeline", self.run_active_pipeline).pack(side="left", padx=4)
        self._add_action_button(toolbar, "Clear log", self.clear_log).pack(side="left", padx=4)

        status_frame = ttk.Frame(parent)
        status_frame.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(status_frame, text="Status:").pack(side="left")
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left", padx=(6, 0))

        log_frame = ttk.LabelFrame(parent, text="Log")
        log_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_text = tk.Text(log_frame, wrap="word", height=30)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _build_notes_tab(self, parent: ttk.Frame) -> None:
        text = tk.Text(parent, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert(
            "end",
            "This GUI wraps the stream-focused workflow:\n\n"
            "1. Scan the PDFs and inventory stream types\n"
            "2. Extract raw and decoded stream payloads\n"
            "3. Build a category-specific dictionary from decoded streams\n"
            "4. Benchmark vanilla Brotli on decoded streams\n"
            "5. Benchmark Brotli with dictionary on decoded streams\n"
            "6. Compare initial stored stream bytes vs vanilla Brotli vs dictionary Brotli\n\n"
            "New in v3:\n"
            "- long-running tasks emit a heartbeat every 10 seconds\n"
            "- commands emit a heartbeat every 15 seconds if there is no stdout\n"
            "- the GUI checks for missing extracted files before category-level steps\n"
            "- buttons remain disabled until the task finishes\n"
        )
        text.configure(state="disabled")

    def _add_action_button(self, parent, text, command) -> ttk.Button:
        btn = ttk.Button(parent, text=text, command=command)
        self.action_buttons.append(btn)
        return btn

    def _path_row(self, parent, label, key, row, dir_pick=False, file_pick=False, save_file=False):
        var = tk.StringVar(value=getattr(self.cfg, key))
        self.vars[key] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(parent, textvariable=var, width=74).grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        parent.grid_columnconfigure(1, weight=1)

        def browse():
            chosen = ""
            if dir_pick:
                chosen = filedialog.askdirectory()
            elif save_file:
                chosen = filedialog.asksaveasfilename(defaultextension=".csv")
            elif file_pick:
                chosen = filedialog.askopenfilename()
            if chosen:
                var.set(chosen)

        ttk.Button(parent, text="Browse", command=browse).grid(row=row, column=2, padx=6, pady=4)

    def _entry_row(self, parent, label, key, row):
        var = tk.StringVar(value=getattr(self.cfg, key))
        self.vars[key] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(parent, textvariable=var, width=30).grid(row=row, column=1, sticky="w", padx=6, pady=4)

    def _check_row(self, parent, label, key, row):
        var = tk.BooleanVar(value=getattr(self.cfg, key))
        self.vars[key] = var
        ttk.Checkbutton(parent, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", padx=6, pady=2)

    def sync_cfg(self) -> None:
        for key in AppConfig.__dataclass_fields__:
            if key in self.vars:
                setattr(self.cfg, key, self.vars[key].get())

    def populate_defaults(self) -> None:
        self.sync_cfg()
        work = Path(self.cfg.work_dir)
        self.vars["scan_csv"].set(str(work / "stream_inventory_v2.csv"))
        self.vars["extract_dir"].set(str(work / "extracted"))
        self.vars["dict_dir"].set(str(work / "dicts"))
        self.vars["results_dir"].set(str(work / "results"))
        self._log("Populated work-dir defaults.")

    def save_config(self) -> None:
        self.sync_cfg()
        path = filedialog.asksaveasfilename(defaultextension=".json")
        if not path:
            return
        Path(path).write_text(json.dumps(asdict(self.cfg), indent=2), encoding="utf-8")
        self._log(f"Saved config: {path}")

    def load_config(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*")])
        if not path:
            return
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key, value in data.items():
            if key in self.vars:
                self.vars[key].set(value)
        self.sync_cfg()
        self._log(f"Loaded config: {path}")

    def open_work_dir(self) -> None:
        self.sync_cfg()
        work = Path(self.cfg.work_dir).resolve()
        work.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(work))
        except Exception as exc:
            self._error(f"Failed to open work dir: {exc}")

    def selected_categories(self) -> list[str]:
        self.sync_cfg()
        cats: list[str] = []
        if self.cfg.include_content_streams:
            cats.append("content_streams")
        if self.cfg.include_object_streams:
            cats.append("object_streams")
        if self.cfg.include_xref_streams:
            cats.append("xref_streams")
        if self.cfg.include_font_streams:
            cats.append("font_streams")
        if self.cfg.include_icc_profiles:
            cats.append("icc_profiles")
        if self.cfg.include_tagged_structures:
            cats.append("tagged_structures")
        return cats

    def _set_busy(self, busy: bool, task_name: str = "") -> None:
        self.is_busy = busy
        self.current_task_name = task_name if busy else ""
        if busy:
            self.task_started_at = time.time()
            self._start_task_heartbeat(task_name)
        else:
            self._stop_task_heartbeat()
        for btn in self.action_buttons:
            try:
                if btn.cget("text") == "Clear log":
                    btn.configure(state="normal")
                else:
                    btn.configure(state="disabled" if busy else "normal")
            except Exception:
                pass
        self.status_var.set(f"Running: {task_name}" if busy else "Idle")

    def _start_task_heartbeat(self, task_name: str) -> None:
        self._stop_task_heartbeat()
        stop_event = threading.Event()
        self.task_heartbeat_stop = stop_event

        def loop() -> None:
            while not stop_event.wait(10.0):
                elapsed = time.time() - self.task_started_at
                self._log(f"... still running: {task_name} (elapsed {elapsed:.1f}s)")

        threading.Thread(target=loop, daemon=True).start()

    def _stop_task_heartbeat(self) -> None:
        if self.task_heartbeat_stop is not None:
            self.task_heartbeat_stop.set()
            self.task_heartbeat_stop = None

    def _poll(self) -> None:
        try:
            while True:
                kind, msg = self.queue.get_nowait()
                if kind == "log":
                    self._append_log(msg)
                elif kind == "error":
                    self._append_log(msg)
                    messagebox.showerror(APP_TITLE, msg)
                elif kind == "done":
                    self._append_log(msg)
                    self._set_busy(False)
                elif kind == "failed":
                    self._append_log(msg)
                    self._set_busy(False)
                    messagebox.showerror(APP_TITLE, msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _append_log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _log(self, msg: str) -> None:
        self.queue.put(("log", msg))

    def _error(self, msg: str) -> None:
        self.queue.put(("error", msg))

    def _done(self, msg: str) -> None:
        self.queue.put(("done", msg))

    def _failed(self, msg: str) -> None:
        self.queue.put(("failed", msg))

    def _run_background(self, job, task_name: str) -> None:
        if self.is_busy:
            self._log(f"A task is already running: {self.current_task_name}")
            return
        self._set_busy(True, task_name)
        self._log(f"=== Started: {task_name} ===")

        def wrapped():
            try:
                job()
                elapsed = time.time() - self.task_started_at
                self._done(f"=== Completed: {task_name} in {elapsed:.1f}s ===")
            except Exception:
                self._failed(traceback.format_exc())

        Worker(wrapped).start()

    def run_cmd(self, cmd: list[str], cwd: str | None = None) -> None:
        pretty = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self._log("CMD: " + pretty)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            bufsize=1,
        )
        assert proc.stdout is not None
        q: queue.Queue[str | None] = queue.Queue()

        def reader() -> None:
            try:
                for line in proc.stdout:
                    q.put(line.rstrip())
            finally:
                q.put(None)

        threading.Thread(target=reader, daemon=True).start()
        last_output = time.time()
        last_heartbeat = 0.0
        command_started = time.time()
        reader_done = False

        while True:
            try:
                item = q.get(timeout=1.0)
                if item is None:
                    reader_done = True
                else:
                    self._log(item)
                    last_output = time.time()
            except queue.Empty:
                pass

            now = time.time()
            if now - last_output >= 15.0 and now - last_heartbeat >= 15.0 and proc.poll() is None:
                self._log(f"... command still running, no new stdout for {now - last_output:.1f}s (elapsed {now - command_started:.1f}s)")
                last_heartbeat = now

            if reader_done and proc.poll() is not None:
                break

        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"Command failed with exit code {code}")
        self._log(f"Command completed successfully in {time.time() - command_started:.1f}s.")

    def _require_extracted_category(self) -> None:
        input_dir = self.active_decoded_dir()
        if not input_dir.exists():
            raise RuntimeError(
                f"Decoded input folder does not exist: {input_dir.resolve()}\n"
                f"Run 'Extract streams' first."
            )
        sample_files = list(input_dir.glob("*.bin"))[:1]
        if not sample_files:
            raise RuntimeError(
                f"No decoded .bin files found under: {input_dir.resolve()}\n"
                f"Run 'Extract streams' first, or check your active category and extract path."
            )

    def run_scan(self) -> None:
        def job():
            self.sync_cfg()
            Path(self.cfg.work_dir).mkdir(parents=True, exist_ok=True)
            cmd = [
                self.cfg.python_exe,
                self.cfg.scanner_script,
                "--input-dir", self.cfg.input_dir,
                "--output-csv", self.cfg.scan_csv,
                "--verbose",
            ]
            self.run_cmd(cmd)
            self._log(f"Scan output ready: {Path(self.cfg.scan_csv).resolve()}")
        self._run_background(job, "Scan PDFs")

    def run_extract(self) -> None:
        def job():
            self.sync_cfg()
            Path(self.cfg.extract_dir).mkdir(parents=True, exist_ok=True)
            cmd = [
                self.cfg.python_exe,
                self.cfg.extractor_script,
                "--input-dir", self.cfg.input_dir,
                "--output-dir", self.cfg.extract_dir,
                "--verbose",
            ]
            categories = self.selected_categories()
            for cat in categories:
                cmd += ["--category", cat]
            self._log(f"Extraction will include categories: {', '.join(categories)}")
            self.run_cmd(cmd)
            self._log(f"Extraction output ready: {Path(self.cfg.extract_dir).resolve()}")
            self._log(f"Manifest path: {(Path(self.cfg.extract_dir) / 'manifest.csv').resolve()}")
        self._run_background(job, "Extract streams")

    def active_decoded_dir(self) -> Path:
        self.sync_cfg()
        return Path(self.cfg.extract_dir) / self.cfg.category / "decoded"

    def active_dict_dir(self) -> Path:
        self.sync_cfg()
        return Path(self.cfg.dict_dir) / self.cfg.category

    def active_results_dir(self) -> Path:
        self.sync_cfg()
        return Path(self.cfg.results_dir) / self.cfg.category

    def first_dict_size(self) -> str:
        self.sync_cfg()
        return self.cfg.dict_sizes.split(",")[0].strip()

    def run_build_dict(self) -> None:
        def job():
            self.sync_cfg()
            self._require_extracted_category()
            input_dir = self.active_decoded_dir()
            output_dir = self.active_dict_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                self.cfg.python_exe,
                self.cfg.dict_builder_script,
                "--input-dir", str(input_dir),
                "--output-dir", str(output_dir),
                "--sizes", self.cfg.dict_sizes,
                "--train-pct", self.cfg.train_pct,
                "--seed", self.cfg.seed,
                "--extensions", ".bin",
            ]
            self.run_cmd(cmd)
            self._log(f"Dictionary output ready: {output_dir.resolve()}")
        self._run_background(job, f"Build dictionary ({self.cfg.category})")

    def run_benchmark_vanilla(self) -> None:
        def job():
            self.sync_cfg()
            self._require_extracted_category()
            input_dir = self.active_decoded_dir()
            results_dir = self.active_results_dir()
            results_dir.mkdir(parents=True, exist_ok=True)
            out_csv = results_dir / "metrics_vanilla.csv"
            cmd = [
                self.cfg.python_exe,
                self.cfg.benchmark_script,
                "--input-dir", str(input_dir),
                "--output-csv", str(out_csv),
                "--qualities", self.cfg.qualities,
                "--trials", self.cfg.trials,
                "--extensions", ".bin",
            ]
            self.run_cmd(cmd)
            self._log(f"Vanilla benchmark output ready: {out_csv.resolve()}")
            self._log(f"Vanilla summary path: {out_csv.with_name(out_csv.stem + '_summary.csv').resolve()}")
        self._run_background(job, f"Benchmark vanilla ({self.cfg.category})")

    def run_benchmark_dict(self) -> None:
        def job():
            self.sync_cfg()
            self._require_extracted_category()
            input_dir = self.active_decoded_dir()
            results_dir = self.active_results_dir()
            results_dir.mkdir(parents=True, exist_ok=True)
            dict_size = self.first_dict_size()
            dict_path = self.active_dict_dir() / f"custom_dict_{dict_size}.bin"
            if not dict_path.exists():
                raise RuntimeError(
                    f"Dictionary file not found: {dict_path.resolve()}\n"
                    f"Run 'Build dictionary for active category' first."
                )
            out_csv = results_dir / f"metrics_dict_{dict_size}.csv"
            template = f'"{self.cfg.brotli_exe}" -f -q {{quality}} -D "{{dict}}" -o "{{output}}" "{{input}}"'
            cmd = [
                self.cfg.python_exe,
                self.cfg.benchmark_script,
                "--input-dir", str(input_dir),
                "--output-csv", str(out_csv),
                "--qualities", self.cfg.qualities,
                "--trials", self.cfg.trials,
                "--dictionary", str(dict_path),
                "--encoder-cmd-template", template,
                "--extensions", ".bin",
            ]
            self.run_cmd(cmd)
            self._log(f"Dictionary benchmark output ready: {out_csv.resolve()}")
            self._log(f"Dictionary summary path: {out_csv.with_name(out_csv.stem + '_summary.csv').resolve()}")
        self._run_background(job, f"Benchmark dictionary ({self.cfg.category})")

    def run_compare(self) -> None:
        def job():
            self.sync_cfg()
            results_dir = self.active_results_dir()
            dict_size = self.first_dict_size()
            manifest = Path(self.cfg.extract_dir) / "manifest.csv"
            if not manifest.exists():
                raise RuntimeError(f"Manifest not found: {manifest.resolve()}\nRun 'Extract streams' first.")
            base_csv = results_dir / "metrics_vanilla.csv"
            dict_csv = results_dir / f"metrics_dict_{dict_size}.csv"
            out_prefix = results_dir / f"compare_{dict_size}"
            cmd = [
                self.cfg.python_exe,
                self.cfg.compare_script,
                "--manifest-csv", str(manifest),
                "--base-brotli-csv", str(base_csv),
                "--dict-brotli-csv", str(dict_csv),
                "--output-prefix", str(out_prefix),
                "--dict-label", f"custom_dict_{dict_size}",
            ]
            self.run_cmd(cmd)
            self._log(f"Comparison detail path: {Path(str(out_prefix) + '_detail.csv').resolve()}")
            self._log(f"Comparison summary path: {Path(str(out_prefix) + '_summary.csv').resolve()}")
        self._run_background(job, f"Compare sizes ({self.cfg.category})")

    def run_active_pipeline(self) -> None:
        def job():
            self.sync_cfg()
            self._require_extracted_category()
            self._log(f"Running pipeline for active category: {self.cfg.category}")
            input_dir = self.active_decoded_dir()
            results_dir = self.active_results_dir()
            dict_dir = self.active_dict_dir()
            results_dir.mkdir(parents=True, exist_ok=True)
            dict_dir.mkdir(parents=True, exist_ok=True)
            dict_size = self.first_dict_size()

            self._log("Step 1/4: Build dictionary")
            self.run_cmd([
                self.cfg.python_exe, self.cfg.dict_builder_script,
                "--input-dir", str(input_dir),
                "--output-dir", str(dict_dir),
                "--sizes", self.cfg.dict_sizes,
                "--train-pct", self.cfg.train_pct,
                "--seed", self.cfg.seed,
                "--extensions", ".bin",
            ])
            self._log(f"Step 1/4 complete. Dictionary folder: {dict_dir.resolve()}")

            self._log("Step 2/4: Benchmark vanilla Brotli")
            vanilla_csv = results_dir / "metrics_vanilla.csv"
            self.run_cmd([
                self.cfg.python_exe, self.cfg.benchmark_script,
                "--input-dir", str(input_dir),
                "--output-csv", str(vanilla_csv),
                "--qualities", self.cfg.qualities,
                "--trials", self.cfg.trials,
                "--extensions", ".bin",
            ])
            self._log(f"Step 2/4 complete. Vanilla CSV: {vanilla_csv.resolve()}")

            self._log("Step 3/4: Benchmark Brotli with dictionary")
            dict_path = dict_dir / f"custom_dict_{dict_size}.bin"
            if not dict_path.exists():
                raise RuntimeError(
                    f"Expected dictionary file missing after build step: {dict_path.resolve()}"
                )
            dict_csv = results_dir / f"metrics_dict_{dict_size}.csv"
            template = f'"{self.cfg.brotli_exe}" -f -q {{quality}} -D "{{dict}}" -o "{{output}}" "{{input}}"'
            self.run_cmd([
                self.cfg.python_exe, self.cfg.benchmark_script,
                "--input-dir", str(input_dir),
                "--output-csv", str(dict_csv),
                "--qualities", self.cfg.qualities,
                "--trials", self.cfg.trials,
                "--dictionary", str(dict_path),
                "--encoder-cmd-template", template,
                "--extensions", ".bin",
            ])
            self._log(f"Step 3/4 complete. Dictionary CSV: {dict_csv.resolve()}")

            self._log("Step 4/4: Compare initial vs vanilla vs dictionary sizes")
            manifest = Path(self.cfg.extract_dir) / "manifest.csv"
            out_prefix = results_dir / f"compare_{dict_size}"
            self.run_cmd([
                self.cfg.python_exe, self.cfg.compare_script,
                "--manifest-csv", str(manifest),
                "--base-brotli-csv", str(vanilla_csv),
                "--dict-brotli-csv", str(dict_csv),
                "--output-prefix", str(out_prefix),
                "--dict-label", f"custom_dict_{dict_size}",
            ])
            self._log(f"Step 4/4 complete. Comparison prefix: {out_prefix.resolve()}")
            self._log(f"Active category pipeline finished for: {self.cfg.category}")

        self._run_background(job, f"Run active category pipeline ({self.cfg.category})")


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
