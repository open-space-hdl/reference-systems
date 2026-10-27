# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Interactive lab tool of spfi_compress: one image on channel 0 or 3 with runtime values of your choice.

The tool takes the image dimensions, the error limit value and the runtime parameters of an image for a channel
whose compressor has the runtime configuration and the runtime parameters (ocp0, ocp3), and an image: a test pattern
of the hardware test or a file (raw samples, or PGM/PPM as one or three bands). It computes the expected compressed
image with the reference model of OpenCompact, sends the image over the far end and compares the chunks that come
back with the model (with --device), decodes the compressed image with the decoder of the model and compares it with
the image sent, and shows the compression ratio and views of the original image, the decoded image and their
difference. Without --device it works with the model only (offline). A result can be written as a record (JSON).

It is a lab tool: the evidence of the hardware test is the scripted run of hardware_test.py. After the tool, program
the device again before a scripted run (the runtime registers and the values taken hold those of the last image), or
use "Restore reset values", which commits the values of the generics and compresses one image with them.

Usage: python systems/spfi_compress/sw/compress_gui.py [--device <STAR-Ultra device name>] [--seed N]
"""

import argparse
import base64
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hardware_test as ht  # noqa: E402 - the system, its channels and the steps of the hardware test
from refsys import ocp, ocptest, systest  # noqa: E402

# The fields of the runtime values (name, label); the parameters follow ocp.PARAM_NAMES
LABELS = {"nx": "NX", "ny": "NY", "nz": "NZ", "abs_limit": "absolute error limit", "rel_limit": "relative error limit",
          "user": "user-defined data", "m": "M (sub-frame depth)", "tinc": "log2 t_inc", "vmin": "vmin", "vmax": "vmax",
          "umax": "Umax", "g0": "gamma0", "gstar": "gamma*", "k": "K (accumulator initialisation)",
          "r": "r (reference sample interval)"}
# The image dimensions and error limit values of ocp.ImageConfig
DIMS = ("nx", "ny", "nz", "abs_limit", "rel_limit")


@dataclass
class Result:
    """The result of one image: the values committed, the model configuration, the compressed image of the model,
    the one received (None offline), the decoded image and the maximum errors (None without the decoder), the
    problems found and STATUS after the COMMIT (None offline)."""

    port: int
    values: ocp.ImageConfig
    cfg: dict
    image: list
    expected: bytes
    received: bytes | None = None
    decoded: list | None = None
    max_error: list | None = None
    problems: list[str] = field(default_factory=list)
    status: tuple[bool, bool] | None = None
    refused: bool = False

    @property
    def samples(self) -> int:
        return ocp.samples_per_image(self.cfg)

    @property
    def data(self) -> bytes:
        return self.received if self.received is not None else self.expected

    def summary(self) -> list[str]:
        n, size = self.samples, len(self.data)
        bits = 8 * size / n
        raw = n * ocp.sample_bytes(self.cfg)
        lines = [f"{self.cfg['large_n_x']} x {self.cfg['large_n_y']} x {self.cfg['large_n_z']} samples, "
                 f"{size} bytes compressed: {bits:.3f} bits per sample, ratio {raw / size:.3f}"]
        if self.refused:
            lines.append(f"COMMIT refused: STATUS (CFG_ERROR, CFG_PENDING) {self.status}")
            return lines + self.problems
        if self.received is not None:
            lines.append("received chunks equal the model image" if self.received == self.expected
                         else "received image differs from the model image")
        if self.decoded is None:
            lines.append("decoder of the model not available: no decoding")
        elif not self.problems:
            lines.append("decoded image equals the image sent" if self.cfg["quantizer_fidelity_control_method"] == 0
                         else "decoded image within the maximum error of every sample")
        return lines + self.problems


class Session:
    """The logic of the tool, without widgets: the system and its runtime channels, the values and their check, the
    image, the run on the device or with the model only, the record. transport: the far end (None: offline)."""

    def __init__(self, transport=None, seed: int = 1, system=None):
        self.system = system or ht.System()
        self.ctx = ht.make_context(seed, ht.PROFILES["lab"], system=self.system)
        self.transport = transport
        self.regs = systest.Registers(transport, self.ctx.setup) if transport is not None else None
        self.configured = False
        self.images = 0

    @property
    def offline(self) -> bool:
        return self.regs is None

    def channels(self) -> list:
        """The channels whose compressor has the runtime configuration."""
        return ocptest.split_channels(self.system)[0]

    def channel(self, port: int):
        return self.system.channels[port]

    def fields(self, port: int) -> list[str]:
        """The runtime values of a channel that the tool lets you set."""
        ch = self.channel(port)
        fid = ch.cfg["quantizer_fidelity_control_method"]
        names = ["nx", "ny", "nz"] + ["abs_limit"] * (fid in (1, 3)) + ["rel_limit"] * (fid in (2, 3))
        if ocp.runtime_params(ch.generics):
            names += ocp.used_params(ch.cfg)
        return names

    def defaults(self, port: int) -> ocp.ImageConfig:
        ch = self.channel(port)
        return ocp.build_values(ch.cfg, ocp.runtime_params(ch.generics))

    def values(self, port: int, entries: dict[str, int]) -> ocp.ImageConfig:
        """The runtime values from the entries of the fields; the others keep their values of the generics."""
        base = self.defaults(port)
        dims = {n: entries.get(n, getattr(base, n)) for n in DIMS}
        if base.params is None:
            return ocp.ImageConfig(**dims)
        p = dict(zip(ocp.PARAM_NAMES, base.params)) | {n: v for n, v in entries.items() if n in ocp.PARAM_NAMES}
        if "m" not in entries and p["m"]:
            p["m"] = min(p["m"], dims["nz"])        # M of the generics, at most the NZ of the image
        return ocp.ImageConfig(**dims, params=tuple(p[n] for n in ocp.PARAM_NAMES))

    def problems(self, port: int, values: ocp.ImageConfig) -> list[str]:
        """Why the COMMIT would refuse these values (empty when it takes them): every dimension or error limit value
        that is out of range with the others at their values of the generics, and every runtime parameter that the
        core would not take as it is."""
        ch = self.channel(port)
        if ocp.commit_accepts(ch.cfg, values):
            return []
        base, dims = self.defaults(port), replace(values, params=None)
        out = []
        if not ocp.commit_accepts(ch.cfg, dims):
            out = [f"{LABELS[name]} {getattr(values, name)} is out of range" for name in DIMS
                   if not ocp.commit_accepts(ch.cfg, ocp.ImageConfig(**{n: getattr(values if n == name else base, n)
                                                                        for n in DIMS}))]
        if values.params is not None:
            driven = dict(zip(ocp.PARAM_NAMES, values.params))
            taken = dict(zip(ocp.PARAM_NAMES, ocp.ocp_configs.runtime_params_limit(ch.cfg, values.nz, values.params)))
            out += [f"{LABELS[n]} {driven[n]} is out of range (the core would take {taken[n]})"
                    for n in ocp.used_params(ch.cfg) if driven[n] != taken[n]]
        return out or ["the values are out of range"]

    def pattern(self, port: int, values: ocp.ImageConfig, kind: str, seed: int) -> list:
        """A test pattern of the hardware test with the dimensions of the values."""
        return ocp.test_image(ocp.image_config(self.channel(port).cfg, values), kind, seed)

    def run(self, port: int, values: ocp.ImageConfig, image: list, send_refused: bool = False) -> Result:
        """Compresses one image: the expected image of the model; with the device the COMMIT, the image sent and the
        chunks received, compared with the model; then the decoding with the decoder of the model. A refused value
        is sent only with send_refused, to see the refusal of the device."""
        ch = self.channel(port)
        accepted = ocp.commit_accepts(ch.cfg, values)
        cfg = ocp.image_config(ch.cfg, values) if accepted else ocp.image_config(ch.cfg, self.defaults(port))
        dims = (len(image[0][0]), len(image[0]), len(image))
        if accepted and dims != (values.nx, values.ny, values.nz):
            raise ValueError(f"the image has {dims[0]} x {dims[1]} x {dims[2]} samples, the values "
                             f"{values.nx} x {values.ny} x {values.nz}")
        result = Result(port, values, cfg, image, ocp.compress(cfg, image) if accepted else b"")
        if not accepted:
            result.refused = True
            result.problems = self.problems(port, values)
            if self.offline or not send_refused:
                return result
            self._configure()
            base = self.system.base(ch.compressor)
            ocptest.write_next(self.regs, self.ctx, ch, values)
            self.regs.write(base + ocptest.OCP.CONTROL, ocptest.COMMIT)
            result.status = ocptest.read_status(self.regs, self.ctx, ch)
            self.regs.write(base + ocptest.OCP.STATUS, ocptest.CFG_ERROR)
            return result
        if not self.offline:
            self._configure()
            ocptest.commit(self.regs, self.ctx, ch, values)
            samples = ocp.input_order(cfg, image)
            received = ht.send_images(self.regs, self.ctx, {port: ht.image_sdus(self.ctx, ch, samples)})[port]
            result.received = received[0]
            result.status = ocptest.read_status(self.regs, self.ctx, ch)
            ocptest.check_taken(self.regs, self.ctx, ch)
        self.images += 1
        if ocp.decoder_available():
            try:
                _, result.decoded, result.max_error = ocp.ocp_model.decompress(result.data, tables=cfg)
            except ocp.ocp_model.DecodeError as err:
                result.problems.append(f"the decoder refuses the image: {err}")
                return result
            result.problems += ocp.decode_problems(cfg, result.data, ocp.input_order(cfg, image))
        return result

    def restore(self, port: int) -> Result:
        """Commits the values of the generics and compresses one image with them, so that the runtime registers and
        the values taken are those after a reset."""
        values = self.defaults(port)
        return self.run(port, values, self.pattern(port, values, "scene", 0))

    def _configure(self) -> None:
        """Once per session: the receive APID table of the Space Packet node and the counters cleared (TC-RS-HW-05)."""
        if not self.configured:
            systest.route_inputs(self.regs, self.ctx, continuity=True)
            ht.clear_status(self.regs, self.ctx)
            self.configured = True

    def record(self, directory: Path, result: Result, source: str) -> Path:
        """Writes a result as a JSON record (the format of the records of hardware_test.py: no host name, no
        path): the time, the channel, the values, the image source, the sizes, the comparisons and the compressed
        bytes (base64)."""
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = directory / f"gui_spfi_compress_{stamp}_ch{result.port}.json"
        data = {"tool": "compress_gui", "system": "spfi_compress", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "target": "model" if self.offline else "device", "channel": result.port,
                "compressor": self.channel(result.port).compressor,
                "values": {"nx": result.values.nx, "ny": result.values.ny, "nz": result.values.nz,
                           "abs_limit": result.values.abs_limit, "rel_limit": result.values.rel_limit}
                | (dict(zip(ocp.PARAM_NAMES, result.values.params)) if result.values.params else {}),
                "image": source, "refused": result.refused, "status": result.status,
                "summary": result.summary(), "problems": result.problems,
                "compressed": base64.b64encode(result.data).decode("ascii")}
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return path


# ------------------------------------------------------------------------------------------------
# Image files
# ------------------------------------------------------------------------------------------------
ORDERS = ("BSQ", "BIL", "BIP")


def read_raw(data: bytes, nx: int, ny: int, nz: int, order: str, sample_bytes: int, big_endian: bool) -> list:
    """An image img[z][y][x] from raw samples in the order BSQ, BIL or BIP, unsigned, 1 or 2 bytes per sample."""
    n = nx * ny * nz
    if len(data) != n * sample_bytes:
        raise ValueError(f"{len(data)} bytes, expected {n * sample_bytes} for {nx} x {ny} x {nz} samples of "
                         f"{sample_bytes} bytes")
    values = [int.from_bytes(data[k:k + sample_bytes], "big" if big_endian else "little")
              for k in range(0, len(data), sample_bytes)]
    img = [[[0] * nx for _ in range(ny)] for _ in range(nz)]
    for k, v in enumerate(values):
        if order == "BSQ":
            z, rest = divmod(k, nx * ny)
            y, x = divmod(rest, nx)
        elif order == "BIL":
            y, rest = divmod(k, nx * nz)
            z, x = divmod(rest, nx)
        else:
            y, rest = divmod(k, nx * nz)
            x, z = divmod(rest, nz)
        img[z][y][x] = v
    return img


def read_pnm(data: bytes) -> list:
    """An image img[z][y][x] from a binary PGM (one band) or PPM (three bands) file, 8 or 16 bits per value."""
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[pos:end])
        pos = end
    magic, width, height, maxval = fields[0], int(fields[1]), int(fields[2]), int(fields[3])
    if magic not in (b"P5", b"P6"):
        raise ValueError("only binary PGM (P5) and PPM (P6) files")
    pos += 1
    bands = 1 if magic == b"P5" else 3
    size = 1 if maxval < 256 else 2
    raw = data[pos:pos + width * height * bands * size]
    values = [int.from_bytes(raw[k:k + size], "big") for k in range(0, len(raw), size)]
    return [[[values[(y * width + x) * bands + z] for x in range(width)] for y in range(height)] for z in range(bands)]


def pgm(band: list[list[int]], lo: int | None = None, hi: int | None = None) -> bytes:
    """A band as an 8-bit PGM image for the views, scaled from lo to hi (default: its own minimum and maximum)."""
    flat = [v for row in band for v in row]
    lo = min(flat) if lo is None else lo
    hi = max(flat) if hi is None else hi
    span = max(hi - lo, 1)
    pixels = bytes(max(0, min(255, (v - lo) * 255 // span)) for v in flat)
    return f"P5 {len(band[0])} {len(band)} 255\n".encode("ascii") + pixels


# ------------------------------------------------------------------------------------------------
# Window
# ------------------------------------------------------------------------------------------------
def gui(session: Session) -> None:  # pragma: no cover - interactive
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("spfi_compress: compress one image" + (" (offline, model only)" if session.offline else ""))
    ports = [ch.port for ch in session.channels()]
    port = tk.IntVar(value=ports[0])
    entries: dict[str, tk.StringVar] = {}
    form = ttk.LabelFrame(root, text="Runtime values")
    form.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
    source = tk.StringVar(value="pattern")
    kind = tk.StringVar(value=ocp.KINDS[0])
    seed = tk.StringVar(value="1")
    order = tk.StringVar(value="BSQ")
    width = tk.StringVar(value="2")
    big = tk.BooleanVar(value=True)
    loaded: dict = {}
    jobs: queue.Queue = queue.Queue()
    state = {"result": None, "band": 0}

    def build_form(*_):
        for child in form.winfo_children():
            child.destroy()
        entries.clear()
        p = port.get()
        base = session.defaults(p)
        values = dict(zip(ocp.PARAM_NAMES, base.params)) if base.params else {}
        ttk.Label(form, text="Channel").grid(row=0, column=0, sticky="w")
        row = ttk.Frame(form)
        row.grid(row=0, column=1, sticky="w")
        for q in ports:
            ttk.Radiobutton(row, text=f"{q} ({session.channel(q).compressor})", value=q, variable=port,
                            command=build_form).pack(side="left")
        for k, name in enumerate(session.fields(p), start=1):
            ttk.Label(form, text=LABELS[name]).grid(row=k, column=0, sticky="w")
            entries[name] = tk.StringVar(value=str(values.get(name, getattr(base, name, 0))))
            ttk.Entry(form, textvariable=entries[name], width=10).grid(row=k, column=1, sticky="w")
        ttk.Label(form, textvariable=check_text, foreground="red", wraplength=320).grid(
            row=len(entries) + 1, column=0, columnspan=2, sticky="w")

    check_text = tk.StringVar()

    def current() -> ocp.ImageConfig:
        return session.values(port.get(), {n: int(v.get()) for n, v in entries.items()})

    def check(*_):
        try:
            check_text.set("; ".join(session.problems(port.get(), current())) or "the COMMIT takes these values")
        except ValueError as err:
            check_text.set(str(err))

    image_box = ttk.LabelFrame(root, text="Image")
    image_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
    ttk.Radiobutton(image_box, text="test pattern", value="pattern", variable=source).grid(row=0, column=0, sticky="w")
    ttk.Combobox(image_box, values=ocp.KINDS, textvariable=kind, width=10, state="readonly").grid(row=0, column=1)
    ttk.Label(image_box, text="seed").grid(row=0, column=2)
    ttk.Entry(image_box, textvariable=seed, width=6).grid(row=0, column=3)
    ttk.Radiobutton(image_box, text="file", value="file", variable=source).grid(row=1, column=0, sticky="w")
    file_label = tk.StringVar(value="none")

    def load():
        name = filedialog.askopenfilename(filetypes=[("images", "*.raw *.bin *.pgm *.ppm"), ("all", "*")])
        if not name:
            return
        data = Path(name).read_bytes()
        try:
            if data[:2] in (b"P5", b"P6"):
                img = read_pnm(data)
            else:
                v = current()
                img = read_raw(data, v.nx, v.ny, v.nz, order.get(), int(width.get()), big.get())
        except ValueError as err:
            messagebox.showerror("Image file", str(err))
            return
        loaded.update(image=img, name=Path(name).name)
        nz, ny, nx = len(img), len(img[0]), len(img[0][0])
        for n, value in (("nx", nx), ("ny", ny), ("nz", nz)):
            entries[n].set(str(value))
        file_label.set(f"{Path(name).name}: {nx} x {ny} x {nz}")
        source.set("file")
        check()

    ttk.Button(image_box, text="Load...", command=load).grid(row=1, column=1)
    ttk.Label(image_box, textvariable=file_label).grid(row=1, column=2, columnspan=2, sticky="w")
    ttk.Label(image_box, text="raw: order").grid(row=2, column=0, sticky="w")
    ttk.Combobox(image_box, values=ORDERS, textvariable=order, width=5, state="readonly").grid(row=2, column=1)
    ttk.Label(image_box, text="bytes").grid(row=2, column=2)
    ttk.Combobox(image_box, values=("1", "2"), textvariable=width, width=3, state="readonly").grid(row=2, column=3)
    ttk.Checkbutton(image_box, text="big endian", variable=big).grid(row=2, column=4)

    out = ttk.LabelFrame(root, text="Result")
    out.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=6, pady=6)
    text = tk.Text(out, width=70, height=10)
    text.grid(row=0, column=0, columnspan=3)
    views = [tk.Label(out) for _ in range(3)]
    for k, (view, title) in enumerate(zip(views, ("original", "decoded", "difference"))):
        ttk.Label(out, text=title).grid(row=1, column=k)
        view.grid(row=2, column=k, padx=4)
    band = tk.Scale(out, from_=0, to=0, orient="horizontal", label="band")
    band.grid(row=3, column=0, columnspan=3, sticky="ew")
    photos: list = []

    def show(*_):
        r = state["result"]
        if r is None or not r.image:
            return
        z = min(int(band.get()), len(r.image) - 1)
        original = r.image[z]
        lo, hi = min(min(row) for row in original), max(max(row) for row in original)
        images = [pgm(original, lo, hi)]
        if r.decoded is not None:
            diff = [[a - b for a, b in zip(ra, rb)] for ra, rb in zip(r.decoded[z], original)]
            peak = max(1, max(abs(v) for row in diff for v in row))
            images += [pgm(r.decoded[z], lo, hi), pgm(diff, -peak, peak)]
        zoom = max(1, 256 // max(len(original[0]), len(original)))
        photos.clear()
        for view, data in zip(views, images + [None] * 3):
            if data is None:
                view.configure(image="")
                continue
            photo = tk.PhotoImage(data=data, format="PPM").zoom(zoom)
            photos.append(photo)
            view.configure(image=photo)

    band.configure(command=show)

    def start(task, *args):
        buttons_state("disabled")
        text.delete("1.0", "end")
        text.insert("end", "working...\n")

        def work():
            try:
                jobs.put(("ok", task(*args)))
            except Exception as err:  # noqa: BLE001 - shown in the window
                jobs.put(("error", err))

        threading.Thread(target=work, daemon=True).start()
        root.after(200, poll)

    def poll():
        try:
            kind_, value = jobs.get_nowait()
        except queue.Empty:
            root.after(200, poll)
            return
        buttons_state("normal")
        text.delete("1.0", "end")
        if kind_ == "error":
            text.insert("end", f"{type(value).__name__}: {value}\n")
            return
        state["result"] = value
        text.insert("end", "\n".join(value.summary()) + "\n")
        band.configure(to=max(0, len(value.image) - 1))
        show()

    def compress(send_refused=False):
        try:
            values = current()
        except ValueError as err:
            messagebox.showerror("Values", str(err))
            return
        if session.problems(port.get(), values) and not send_refused:
            check()
            messagebox.showinfo("Values", "The COMMIT would refuse these values; use 'Send refused values' to see "
                                "the refusal of the device.")
            return
        if source.get() == "file" and loaded:
            image = loaded["image"]
        else:
            image = session.pattern(port.get(), values if not session.problems(port.get(), values)
                                    else session.defaults(port.get()), kind.get(), int(seed.get()))
        start(session.run, port.get(), values, image, send_refused)

    def record():
        r = state["result"]
        if r is None:
            return
        directory = filedialog.askdirectory(title="Directory of the records")
        if directory:
            name = loaded.get("name", "") if source.get() == "file" else f"pattern {kind.get()} seed {seed.get()}"
            path = session.record(Path(directory), r, name)
            messagebox.showinfo("Record", f"written {path.name}")

    buttons = ttk.Frame(root)
    buttons.grid(row=2, column=0, sticky="w", padx=6, pady=6)
    actions = [("Check", check), ("Compress", compress),
               ("Send refused values", lambda: compress(True)),
               ("Restore reset values", lambda: start(session.restore, port.get())), ("Record...", record)]
    widgets = [ttk.Button(buttons, text=t, command=c) for t, c in actions]
    for w in widgets:
        w.pack(side="left", padx=2)
    if session.offline:
        widgets[2].state(["disabled"])

    def buttons_state(value):
        for w in widgets:
            w.configure(state=value)
        if session.offline:
            widgets[2].state(["disabled"])

    build_form()
    check()
    root.mainloop()


def main() -> int:  # pragma: no cover - interactive
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="name of the STAR-Ultra device in STAR-System (without it: offline)")
    parser.add_argument("--seed", type=int, default=1, help="seed of the Space Packets of the session")
    args = parser.parse_args()
    if args.device is None:
        gui(Session(seed=args.seed))
        return 0
    from refsys.transport import StarSystemTransport

    with StarSystemTransport(args.device) as transport:
        session = Session(transport, args.seed)
        systest.drain(transport, (session.ctx.setup.rmap_channel, session.ctx.setup.spp_channel))
        gui(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
