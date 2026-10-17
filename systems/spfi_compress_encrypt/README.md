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
| Composer file | not yet: it needs the OpenCompact channel (`ocp_channel`) in the catalog of the Composer |
| Generated top level (`hdl/`) | not yet |
| System testbench (`sim/`) | not yet |
| AMD VCK190 target | not yet; the board top, the constraints and the build script follow those of spfi_encrypt |
| Hardware test ([procedure](targets/vck190/docs/hardware_test.md), [sw/hardware_test.py](sw/hardware_test.py)) | test cases against a STAR-Ultra PCIe as the far end, checked against a software model of the system on a stand-in for its Composer file; not run on hardware yet |

## Blocks

| Instance | Entity | Function |
| --- | --- | --- |
| `pa0`, `ofb0`, `nchar0`, `rmap0`, `spp0` | as in spfi_encrypt | GTY physical adapter, SpaceFibre port with 4 lanes and 2 virtual channels, N-Char bridge and RMAP target on virtual channel 0, Space Packet node on virtual channel 1 with 4 send and 4 receive ports |
| `ocp0` to `ocp3` | `opencompact.ocp_channel` | OpenCompact channel: input adapter (SDUs to samples, end mark, faulty SDUs), CCSDS 123.0-B-2 compressor (`ocp_core`), output adapter and a read-only status interface (configuration, image and SDU counters, memory errors) |
| `frm0` to `frm3` | `opencrypto.ocr_framer` | Cuts every compressed image into chunks of 4096 bytes and creates one encryption per chunk |
| `ocr0` to `ocr3` | `opencrypto.ocr_core` | AES-GCM cores, 128-bit data path, one per channel |
| `dec0` | `olo.olo_axi_lite_decoder` | Splits the AXI4-Lite master of `rmap0` into the register windows |
| `edac0` | `olo.olo_ft_ecc_monitor_axi` | Counts the corrected and uncorrectable errors of the framer memories |

## Data paths

- **RMAP:** virtual channel 0 of `ofb0` ↔ `nchar0` ↔ `rmap0`, as in spfi_encrypt.
- **Channel i (i = 0 to 3):** virtual channel 1 of `ofb0` → `spp0` receive port i → `ocp<i>` → `frm<i>` → `ocr<i>` →
  `spp0` send port i with APID i → virtual channel 1. The receive APID table of `spp0` selects the channel of a
  received Space Packet. The AES-GCM core of a channel needs a loaded key slot with generated IVs before the first
  image.

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
compressors: one device image has one configuration per channel.

## Clocks and reset

As in spfi_encrypt: `Clk_sys` for all blocks except the lane clock domain of `ofb0`, the OpenCompact channels
included; `Rst` is the asynchronous, high-active reset of the system.

## Test software

The hardware test computes the expected compressed image of every test image with the reference model of OpenCompact
(`tools/ocp_model.py` of the submodule `cores/opencompact`) from the generics of the compressor of its channel in the
Composer file, and the images and counters of every channel after lost SDUs with a model of the OpenCompact channel
(`sw/refsys/ocp.py`). The test images are deterministic for a seed and depend on every bit of the sample address, so
that an address fault of a memory of a compressor changes its compressed image.
