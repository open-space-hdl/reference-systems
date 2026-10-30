# spfi_compress_encrypt

A SpaceFibre node that compresses and encrypts images. One SpaceFibre port with four lanes carries RMAP commands on
virtual channel 0 and Space Packets on virtual channel 1. Four channels take the samples of multispectral or
hyperspectral images from received Space Packets, compress every image with CCSDS 123.0-B-2 (OpenCompact), encrypt
the compressed image in chunks with AES-GCM (OpenCrypto) and send every encrypted chunk back as a Space Packet with
the APID of its channel. An RMAP target gives access to the registers of all blocks over the same link.

The system extends [spfi_encrypt](../spfi_encrypt/README.md) by a compression stage in front of every framer; link,
RMAP node, Space Packet node, framers and AES-GCM cores are those of spfi_encrypt.

## Status

| Item | Status |
| --- | --- |
| Composer file ([spfi_compress_encrypt.composer.json](spfi_compress_encrypt.composer.json)) | complete; the Composer reports one warning per channel, see [Aborted images](#aborted-images) |
| Generated top level (`hdl/`) | exported from the Composer file |
| System testbench ([sim/](sim/README.md)) | TC-RS-HW-01 to 06, 10 and 18 pass in GHDL at the system clock of 150 MHz, TC-RS-HW-06 with images of the size of the lab; TC-RS-HW-08 at the image size of the lab passed with the cores of v0.1.0; TC-RS-HW-01 to 05 pass in xsim with the AMD transceiver model at the clocks of the default device image |
| AMD VCK190 target ([targets/vck190](targets/vck190/README.md)) | board top level, constraints and build script as in spfi_encrypt. Not yet built from this release; synthesis, timing and the hardware test follow, with results in a later release |
| Hardware test ([procedure](targets/vck190/docs/hardware_test.md), [sw/hardware_test.py](sw/hardware_test.py)) | test cases against a STAR-Ultra PCIe as the far end, checked against a software model of the system; not run on hardware yet |

## Blocks

| Instance | Entity | Function |
| --- | --- | --- |
| `pa0`, `ofb0`, `nchar0`, `rmap0`, `spp0` | as in spfi_encrypt | GTY physical adapter, SpaceFibre port with 4 lanes and 2 virtual channels, N-Char bridge and RMAP target on virtual channel 0, Space Packet node on virtual channel 1 with 4 send and 4 receive ports |
| `rxf0` to `rxf3` | `olo.olo_ft_fifo_sync` | FIFO in front of every compressor: two of the longest Space Packets of the receive buffer of `spp0` (1024 words of 167 bits: data, byte enables, last flag and user signal of the SDUs), memory protected by SECDED |
| `ocp0` to `ocp3` | `opencompact.ocp_channel` | OpenCompact channel: input adapter (SDUs to samples, end mark, faulty SDUs), CCSDS 123.0-B-2 compressor (`ocp_core`), output adapter and a read-only status interface (configuration, image and SDU counters, memory errors) |
| `frm0` to `frm3` | `opencrypto.ocr_framer` | Cuts every compressed image into chunks of 4096 bytes and creates one encryption per chunk |
| `ocr0` to `ocr3` | `opencrypto.ocr_core` | AES-GCM cores, 128-bit data path, one per channel |
| `dec0` | `olo.olo_axi_lite_decoder` | Splits the AXI4-Lite master of `rmap0` into the register windows |
| `edac0` | `olo.olo_ft_ecc_monitor_axi` | Counts the corrected and uncorrectable errors of the memories of the framers (channels 0 to 7, as in spfi_encrypt), of the compressors (channels 8 to 11) and of the FIFOs (channels 12 to 15) |

## Data paths

- **RMAP:** virtual channel 0 of `ofb0` ↔ `nchar0` ↔ `rmap0`, as in spfi_encrypt.
- **Channel i (i = 0 to 3):** virtual channel 1 of `ofb0` → `spp0` receive port i → `rxf<i>` → `ocp<i>` → `frm<i>` →
  `ocr<i>` → `spp0` send port i with APID i → virtual channel 1. The receive APID table of `spp0` selects the channel
  of a received Space Packet. The AES-GCM core of a channel needs a loaded key slot with generated IVs before the
  first image.

## Images on the link

- **Samples.** The Packet Data Field of a Space Packet (an SDU of the Octet String Service) carries a whole number of
  samples of one image in the input order of the compressor of its channel, which is the encoding order of the
  channel (CCSDS 123.0-B-2 5.4.2): BSQ band by band, BI row by row with groups of M bands per pixel. Every sample
  takes ceil(D/8) bytes, most significant byte first, the value in the D least significant bits.
- **End of an image.** The last SDU of an image carries the User Application value 0x01 in its CPTP packet
  (ECSS-E-ST-50-53C 5.2.3), all other SDUs 0x00. `spp0` delivers this value with every SDU, and `ocp<i>` ends the image
  with the last sample of the marked SDU.
- **Compressed images.** Every compressed image (header, body and fill bits up to a multiple of B bytes) leaves the
  compressor as one packet. The framer cuts it into chunks of 4096 bytes; the position of a chunk in its image (first,
  continuation, last or the only chunk) is in the authenticated data of the chunk, so the receiver finds the image
  boundaries from the chunks. Every chunk leaves as one Space Packet with at most 4128 bytes of user data (IV 12,
  position 4, ciphertext, tag 16).
- **Lost data.** The receive APID table checks the Packet Sequence Count of every input APID, so the SDU after a gap
  carries the Data Loss Indicator. `ocp<i>` treats such an SDU, and an SDU read with a double bit error from the
  receive buffer of `spp0`, as faulty: it ends the image with an error, discards the rest of the image up to the next
  end mark and compresses the following images normally. A compressed image with an error leaves like any other; its
  channel counts it in `IMAGES_ABORTED` of its status interface (OpenCompact, specification of `ocp_channel`,
  section 3).

## Aborted images

The framer `frm<i>` has no input for the error flag of the compressed images (`Out_Error` of `ocp<i>` on the last beat
of an image): an image that ended with an error is cut into chunks and sent like a good one. The Composer reports this
once per channel (rule `flag_dropped` of the catalog entry of `ocp_channel`). The system counts these images in
`IMAGES_ABORTED` of the status interface of the channel, which the far end reads over RMAP; the hardware test checks
the counter after lost Space Packets (TC-RS-HW-08).

## Lab configurations

The four channels compress images of 64 x 64 samples in 48 bands with 16-bit samples, each with one family of the
configurations that OpenCompact verified and synthesised (OpenCompact user guide, resources). One image is 196,608
samples, 393,216 bytes on the link: 49 SDUs, the longest Space Packet that the receive buffer of `spp0` holds (8186
bytes of data) 48 times and 288 bytes.

| Channel | Family | Order | Prediction | Quantizer | Entropy coder |
| --- | --- | --- | --- | --- | --- |
| 0 | c01 | BIP (M = 48) | full, P = 3, wide neighbour-oriented local sums, Omega = 13, R = 32 | lossless | sample-adaptive, Umax = 18, K = 4 |
| 1 | c25 | BSQ | reduced, P = 3, narrow neighbour-oriented local sums, Omega = 19, R = 48 | lossless | sample-adaptive, Umax = 18, K = 5 |
| 2 | c27 | BIP (M = 48) | full, P = 3, wide neighbour-oriented local sums, Omega = 19, R = 64 | lossless | block-adaptive, J = 16, r = 32 |
| 3 | c26 | BIL (M = 1) | reduced, P = 3, narrow column-oriented local sums, Omega = 19, R = 48 | near-lossless, absolute error limit 12 (DA = 10), sample representatives Theta = 2 | hybrid, Umax = 16, gamma0 = 2 |

All four use an output word size of 4 bytes and image framing by the end mark. The parameters are generics of the
compressors, except on channels 0 and 3, whose compressors have the runtime configuration of OpenCompact
(`RuntimeConfig_g`, OpenCompact user guide, section 4.4) and its runtime parameters (`RuntimeParams_g`, section
4.5). Software sets over RMAP the image dimensions of every image on both channels, up to the 64 x 64 x 48 of the
table, on channel 3 also the absolute error limit value, 0 to 1023, and the runtime parameters of every image: the
user-defined data, M, the weight update parameters and the parameters of the entropy coder. It writes NEXT_X, NEXT_Y,
NEXT_Z and NEXT_LIMITS, then NEXT_SAMPLES to NEXT_BLOCK, and then CONTROL.COMMIT. STATUS shows a refused (CFG_ERROR)
or pending (CFG_PENDING) configuration, and IMAGE_X to IMAGE_Z, SAMPLES, ORDER, WEIGHTS, ADAPTIVE, BLOCK and LIMITS
show the values of the last image that started (OpenCompact register map). After a reset both channels compress with
the values of the table. Channel 0 is lossless, so it has no error limit to change; with the values of the reset the
sub-frame depth of its BIP order follows the number of bands (M = min(48, NZ)). Only channel 3, with reduced
prediction and column-oriented local sums, takes images one sample wide. A compressor with the runtime configuration
needs 8 to 10 clock cycles more per image; the throughput figures below take channels 0 and 3 at the upper bound of
their compressed images over the ranges of the runtime parameters. Channels 1 and 2 keep the configuration of their
generics: one device image holds both kinds.

## Throughput

The four compressors run in parallel. Each takes one sample per clock cycle in the lab configurations (OpenCompact,
`tools/ocp_throughput.py`: 196,608 cycles per image of 64 x 64 x 48 samples), 2 bytes per cycle: an SDU of 8186
bytes in 27.3 us, 2.4 Gbit/s per channel at 150 MHz. Each writes the compressed image in words of 4 bytes, at most
one per cycle, so that a compressor keeps this rate as long as its compressed image takes at most 32 bits per sample.
The upper bounds on the compressed image that OpenCompact publishes (`max_image_bytes` of its model; on channels 0 and
3 its maximum over the ranges of the runtime parameters, at Umax 32 and gamma\* 4) are higher for three of the
configurations, and with them the output limits the rate of a channel:

| Channel | Upper bound on the compressed image | Time per SDU of 8186 bytes at that bound |
| --- | --- | --- |
| 0 (c01, sample-adaptive) | 48.0 bits per sample (34.0 with the parameters of the generics) | 40.9 us (29.0 us) |
| 1 (c25, sample-adaptive) | 34.0 bits per sample | 29.0 us |
| 2 (c27, block-adaptive) | 16.25 bits per sample | 27.3 us (the input) |
| 3 (c26, hybrid) | 69.1 bits per sample (53.0 with the parameters of the generics) | 58.9 us (45.2 us) |

When the Space Packets are spread evenly over the channels, the slowest channel sets the rate of all (see below):
9.6 Gbit/s for the four channels when every compressed image takes at most 32 bits per sample (at most twice the
size of its samples), 4.4 Gbit/s (4 x 65.5 kbit in 58.9 us) at the upper bound of channel 3. The receive side does
not limit it: the four lanes of `ofb0` carry 20 Gbit/s of 8B/10B data (6.25 Gbit/s per lane), the receive path of
`spp0` 16 bytes per cycle. On the send side the four send ports of `spp0` share one path to the link; the throughput
budget of the Composer rates every path on its own and gives no rate of the four together.

The receive buffer of `spp0` (8192 bytes) is shared by all receive ports and delivers the Space Packets in the order
in which they arrived (OpenSPP user guide, generics `RxBufferBytes_g`): a packet leaves the buffer only after the
packet before it, of any channel, has been taken. Without the FIFOs the channels would take turns: one SDU of 8186
bytes fills the buffer, and the next SDU, of any channel, would wait until the compressor of the previous one had
taken it (4093 cycles, 27.3 us), so the system would compress at the rate of one channel. `rxf<i>` takes the SDUs of
its channel at the rate of the receive port and holds two of them, so the buffer of `spp0` empties at the rate of the
link while the compressors work.

The throughput budget of the Composer (section "Throughput" of the brief in the system file) rates every path from
the link through a channel back to the link at the upper bound of its compressed images, on channels 0 and 3 the
bound over the ranges of the runtime parameters:

| Path | Packets of 64 bytes | Packets of 1024 bytes | Limited by |
| --- | --- | --- | --- |
| Channel 0 | 2.05 Gbit/s | 1.62 Gbit/s | `ocp0.Out` |
| Channel 1 | 2.89 Gbit/s | 2.29 Gbit/s | `ocp1.Out` |
| Channel 2 | 2.95 Gbit/s | 2.43 Gbit/s | `ocp2.In` |
| Channel 3 | 1.42 Gbit/s | 1.13 Gbit/s | `ocp3.Out` |

It also rates the four receive ports of `spp0` together, with the packets spread evenly over them: delivered in the
order of arrival they take 5.69 Gbit/s at packets of 64 bytes and 4.51 Gbit/s at 1024 bytes, four times the slowest
path, since a packet holds the delivery until its output has taken it. A store of two packets behind every output
gives the same rates.

What remains: when the FIFO of one channel is full, because its compressor is two SDUs behind, the next Space Packet
of that channel stays in the buffer of `spp0` and holds back the packets of all channels behind it until the
compressor has taken one SDU. A sender that interleaves the channels evenly, as the hardware test does, keeps the
FIFOs from filling up as long as all four compressors take one sample per cycle; a channel that receives more than
its share, or whose compressed images exceed 32 bits per sample, slows the others down to its own rate.

## Clocks and reset

As in spfi_encrypt: `Clk_sys` for all blocks except the lane clock domain of `ofb0`, the OpenCompact channels
included; `Rst` is the asynchronous, high-active reset of the system.

## Lane initialisation

As in spfi_encrypt ([lane initialisation](../spfi_encrypt/README.md#lane-initialisation)): the lane settings of the
OpenFibre reference design for the VCK190, a receiver reset of a lane at every restart and an initialisation timeout of
468750 words (3 ms), which deviates from ECSS-E-ST-50-11C 5.5.2.6b.1 and 5.5.2.7b.1; the bounds of the waits of `pa0`
scaled to the 150 MHz of `Clk_sys`.

## Test software

The hardware test computes the expected compressed image of every test image with the reference model of OpenCompact
(`tools/ocp_model.py` of the submodule `cores/opencompact`) from the generics of the compressor of its channel in the
Composer file, and the images and counters of every channel after lost SDUs with a model of the OpenCompact channel
(`sw/refsys/ocp.py`). The test images are deterministic for a seed and depend on every bit of the sample address, so
that an address fault of a memory of a compressor changes its compressed image.

TC-RS-HW-18 runs the test cases of the run again on the same device without a new device image, as a second run of the
script does: every run reads the Packet Sequence Counts from which its Space Packets continue and starts its compressors
with the values of the generics (hardware test procedure, section "Repeated runs").

TC-RS-HW-10 tests the runtime configuration (`sw/refsys/ocptest.py`). Before every image of its plan, channels 0 and 3
get the values of that image. The first COMMIT is followed by COMMITs with one value out of range, which the
compressor must refuse with CFG_ERROR while the configuration stays pending. Every compressed image is compared with
the image of the reference model for the values committed before it; the configuration of each image comes from
`runtime_image_cfg` of OpenCompact (`tools/ocp_configs.py`), which builds the expected images of its own runtime
testbench. Channels 1 and 2 must ignore the writes of the runtime registers.
