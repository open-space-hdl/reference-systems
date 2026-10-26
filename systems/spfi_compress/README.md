# spfi_compress

A SpaceFibre node that compresses images. One SpaceFibre port with four lanes carries RMAP commands on virtual
channel 0 and Space Packets on virtual channel 1. Four channels take the samples of multispectral or hyperspectral
images from received Space Packets, compress every image with CCSDS 123.0-B-2 (OpenCompact), cut the compressed image
into chunks of 4096 bytes and send every chunk back as a Space Packet with the APID of its channel. An RMAP target
gives access to the registers of all blocks over the same link.

The system is [spfi_compress_encrypt](../spfi_compress_encrypt/README.md) without its encryption: link, RMAP node,
Space Packet node, FIFOs and compressors are those of spfi_compress_encrypt, and a chunker of Open Logic takes the
place of the framer and the AES-GCM core of every channel.

## Status

| Item | Status |
| --- | --- |
| Composer file ([spfi_compress.composer.json](spfi_compress.composer.json)) | complete; the Composer reports one warning per channel, see [Aborted images](#aborted-images) |
| Generated top level (`hdl/`) | exported from the Composer file |
| System testbench ([sim/](sim/README.md)) | TC-RS-HW-05, 06 and 08 pass in GHDL at the image size of the lab, TC-RS-HW-01 to 06 and 10 at the system clock of 150 MHz; TC-RS-HW-01 to 05 pass in xsim with the AMD transceiver model |
| AMD VCK190 target ([targets/vck190](targets/vck190/README.md)) | board top level, constraints and build script as in spfi_compress_encrypt; not built yet |
| Hardware test ([procedure](targets/vck190/docs/hardware_test.md), [sw/hardware_test.py](sw/hardware_test.py)) | test cases against a STAR-Ultra PCIe as the far end, checked against a software model of the system; not run on hardware yet |

## Blocks

| Instance | Entity | Function |
| --- | --- | --- |
| `pa0`, `ofb0`, `nchar0`, `rmap0`, `spp0` | as in spfi_encrypt | GTY physical adapter, SpaceFibre port with 4 lanes and 2 virtual channels, N-Char bridge and RMAP target on virtual channel 0, Space Packet node on virtual channel 1 with 4 send and 4 receive ports |
| `rxf0` to `rxf3` | `olo.olo_ft_fifo_sync` | FIFO in front of every compressor, as in spfi_compress_encrypt: two of the longest Space Packets of the receive buffer of `spp0`, memory protected by SECDED |
| `ocp0` to `ocp3` | `opencompact.ocp_channel` | OpenCompact channel: input adapter (SDUs to samples, end mark, faulty SDUs), CCSDS 123.0-B-2 compressor (`ocp_core`), output adapter and a read-only status interface (configuration, image and SDU counters, memory errors) |
| `chunk0` to `chunk3` | `olo.olo_base_chunker` | Cuts every compressed image into chunks of 256 beats of 128 bits (4096 bytes); the last chunk of an image holds the rest |
| `dec0` | `olo.olo_axi_lite_decoder` | Splits the AXI4-Lite master of `rmap0` into the register windows |
| `edac0` | `olo.olo_ft_ecc_monitor_axi` | Counts the corrected and uncorrectable errors of the memories of the compressors (channels 0 to 3) and of the FIFOs (channels 4 to 7) |

The register windows follow each other without gaps (`hdl/spfi_compress_address_map.md`): `spp0`, `edac0`, `rmap0`,
`ofb0` and `ocp0` to `ocp3` at 0x00000 to 0x70000, 64 KiB each.

## Data paths

- **RMAP:** virtual channel 0 of `ofb0` ↔ `nchar0` ↔ `rmap0`, as in spfi_encrypt.
- **Channel i (i = 0 to 3):** virtual channel 1 of `ofb0` → `spp0` receive port i → `rxf<i>` → `ocp<i>` →
  `chunk<i>` → `spp0` send port i with APID i → virtual channel 1. The receive APID table of `spp0` selects the
  channel of a received Space Packet. No key or other configuration is needed before the first image.

## Images on the link

- **Samples.** As in spfi_compress_encrypt: the Packet Data Field of a Space Packet (an SDU of the Octet String
  Service) carries a whole number of samples of one image in the input order of the compressor of its channel, which
  is the encoding order of the channel (CCSDS 123.0-B-2 5.4.2). Every sample takes ceil(D/8) bytes, most significant
  byte first, the value in the D least significant bits.
- **End of an image.** The last SDU of an image carries the User Application value 0x01 in its CPTP packet
  (ECSS-E-ST-50-53C 5.2.3), all other SDUs 0x00. `spp0` delivers this value with every SDU, and `ocp<i>` ends the image
  with the last sample of the marked SDU.
- **Compressed images.** Every compressed image (header, body and fill bits up to a multiple of B = 4 bytes) leaves
  the compressor as one packet. The chunker cuts it into chunks of 4096 bytes, the last one with the remaining 4 to
  4096 bytes, and every chunk leaves as one Space Packet of the Octet String Service: 4096 bytes of user data at most,
  the Sequence Flags '11' (unsegmented, OpenSPP user guide 4.1), which the Octet String Service always sets
  (CCSDS 133.0-B-2 4.1.3.4.2.3), and a Packet Sequence Count per APID.
- **No mark of the image end.** Every compressed image starts in a new Space Packet, and no Space Packet carries
  bytes of two images. The Space Packets do not mark the last chunk of an image: the last chunk is shorter than 4096
  bytes unless the length of the compressed image is a multiple of 4096. A receiver finds the end of an image by
  decoding it: the header of the compressed image gives the image dimensions NX, NY and NZ (CCSDS 123.0-B-2 5.3),
  and the body ends with the fill bits up to the next multiple of B bytes after the last sample (5.2.2). An image that
  ended with an error is shorter than its header says (see [Aborted images](#aborted-images)).
- **Lost data.** As in spfi_compress_encrypt: the receive APID table checks the Packet Sequence Count of every input
  APID, so the SDU after a gap carries the Data Loss Indicator. `ocp<i>` treats such an SDU, and an SDU read with a
  double bit error from the receive buffer of `spp0`, as faulty: it ends the image with an error, discards the rest
  of the image up to the next end mark and compresses the following images normally. Its channel counts the image in
  `IMAGES_ABORTED` of its status interface (OpenCompact, specification of `ocp_channel`, section 3).

One compressed image may need many Space Packets: a Packet Data Field holds at most 65,536 bytes (CCSDS 133.0-B-2
4.1.2.1), and the upper bounds on the compressed images of the lab configurations (`max_image_bytes` of the model of
OpenCompact) are larger:

| Channel | Upper bound on the compressed image | Space Packets of 4096 bytes at that bound | Space Packets at 65,536 bytes |
| --- | --- | --- | --- |
| 0 (c01, sample-adaptive) | 835,496 bytes (34.0 bits per sample) | 204 | 13 |
| 1 (c25, sample-adaptive) | 835,496 bytes (34.0 bits per sample) | 204 | 13 |
| 2 (c27, block-adaptive) | 399,380 bytes (16.25 bits per sample) | 98 | 7 |
| 3 (c26, hybrid) | 1,303,256 bytes (53.0 bits per sample) | 319 | 20 |

The chunks of 4096 bytes leave room for two of them in the send buffer of every port of `spp0` (8192 bytes): one
chunk enters while the one before it leaves.

## Aborted images

The chunker has no input for the error flag of the compressed images (`Out_Error` of `ocp<i>` on the last beat of
an image): an image that ended with an error is cut into chunks and sent like a good one. The Composer reports this
once per channel (rule `flag_dropped` of the catalog entry of `ocp_channel`). An aborted image has the header of the
complete image and the codewords of the samples taken (OpenCompact, CO-FR-03), so a decoder that reads it to its
header dimensions runs into the next image. The system counts these images in `IMAGES_ABORTED` of the status
interface of the channel, which the far end reads over RMAP; the hardware test checks the counter after lost Space
Packets (TC-RS-HW-08).

## Planned improvement

A later version marks the last chunk of every image on the link, an aborted image included, so that a receiver
finds the image boundaries without decoding. The Octet String Service, which the send ports use now, does not
segment: its Space Packets always carry the Sequence Flags '11' (CCSDS 133.0-B-2 4.1.3.4.2.3). The mark is either the
Sequence Flags of segmented user data ('01' first, '00' continuation, '10' last chunk, '11' an image of one chunk,
4.1.3.4.2.2) in Space Packets that the channel formats and sends through a send port of the Packet Service, or the
position of the chunk in a Packet Secondary Header at the start of its Octet String (4.1.4.2).

## Lab configurations

As in spfi_compress_encrypt: the four channels compress images of 64 x 64 samples in 48 bands with 16-bit samples,
each with one family of the configurations that OpenCompact verified and synthesised (OpenCompact user guide,
resources). One image is 196,608 samples, 393,216 bytes on the link: 49 SDUs, the longest Space Packet that the
receive buffer of `spp0` holds (8186 bytes of data) 48 times and 288 bytes.

| Channel | Family | Order | Prediction | Quantizer | Entropy coder |
| --- | --- | --- | --- | --- | --- |
| 0 | c01 | BIP (M = 48) | full, P = 3, wide neighbour-oriented local sums, Omega = 13, R = 32 | lossless | sample-adaptive, Umax = 18, K = 4 |
| 1 | c25 | BSQ | reduced, P = 3, narrow neighbour-oriented local sums, Omega = 19, R = 48 | lossless | sample-adaptive, Umax = 18, K = 5 |
| 2 | c27 | BIP (M = 48) | full, P = 3, wide neighbour-oriented local sums, Omega = 19, R = 64 | lossless | block-adaptive, J = 16, r = 32 |
| 3 | c26 | BIL (M = 1) | reduced, P = 3, narrow column-oriented local sums, Omega = 19, R = 48 | near-lossless, absolute error limit 12 (DA = 10), sample representatives Theta = 2 | hybrid, Umax = 16, gamma0 = 2 |

All four use an output word size of 4 bytes and image framing by the end mark. The parameters are generics of the
compressors, except on channels 0 and 3, whose compressors have the runtime configuration of OpenCompact
(`RuntimeConfig_g`, OpenCompact user guide, section 4.4) and its runtime parameters (`RuntimeParams_g`, section
4.5: the user-defined data, M, the weight update parameters and the parameters of the entropy coder of every
image). Software sets the image dimensions of every image on both
channels over RMAP, up to the 64 x 64 x 48 of the table. On channel 3 it also sets the absolute error limit value, 0
to 1023. It writes NEXT_X, NEXT_Y, NEXT_Z and NEXT_LIMITS and then CONTROL.COMMIT. STATUS shows a refused (CFG_ERROR)
or pending (CFG_PENDING) configuration, and IMAGE_X to IMAGE_Z, ORDER and LIMITS show the values of the last image
that started (OpenCompact register map). After a reset both channels compress with the values of the table. Channel 0
is lossless, so it has no error limit to change; in its BIP order the sub-frame depth follows the number of bands (M =
min(48, NZ)). Only channel 3, with reduced prediction and column-oriented local sums, takes images one sample wide. A
compressor with the runtime configuration needs 8 to 10 clock cycles more per image, so the throughput figures below
also hold for channels 0 and 3. Channels 1 and 2 keep the configuration of their generics: one device image holds both
kinds.

## Throughput

The receive side and the compressors are those of spfi_compress_encrypt
([its README](../spfi_compress_encrypt/README.md#throughput), section Throughput): every compressor takes one sample
per clock cycle, 2.4 Gbit/s per channel at 150 MHz, and writes at most one word of 4 bytes per cycle, so that it
keeps this rate as long as its compressed image takes at most 32 bits per sample. At the upper bounds of the table
above the output limits channels 0, 1 and 3 (29.0 us, 29.0 us and 45.2 us per SDU of 8186 bytes instead of 27.3 us).
With the Space Packets spread evenly over the channels, the four channels take 9.6 Gbit/s of samples when every
compressed image takes at most 32 bits per sample, and 5.8 Gbit/s at the upper bound of channel 3; the FIFOs `rxf<i>`
keep the shared receive buffer of `spp0` from holding back the channels, as in spfi_compress_encrypt.

The throughput budget of the Composer (section "Throughput" of the brief in the system file) gives every channel the
rate of its compressor at the upper bound of its compressed images. At input packets of 1024 bytes that is 2.29 Gbit/s
of samples on channels 0 and 1 and 1.47 Gbit/s on channel 3, limited by the output of the compressor, and
2.43 Gbit/s on channel 2, limited by its input. The four receive ports together take 5.87 Gbit/s with the packets
spread evenly over them, as in spfi_compress_encrypt. The chunker and the send side carry more on every path.

The budget rates every path on its own. The four send ports of `spp0` share one path to the link, and the channels
together do not exceed it either:

- The chunker passes every beat in the cycle in which it arrives (Open Logic documentation of `olo_base_chunker`,
  section Timing): it adds neither cycles nor latency.
- Every compressor writes at most 4.8 Gbit/s (4 bytes per cycle at 150 MHz). At the upper bounds the four channels
  send 16.8 Gbit/s of compressed data together, three of them limited by their output and channel 2 by its input.
- The send ports of `spp0` share one path to the link of 16 bytes per cycle with one cycle per packet more (OpenSPP
  user guide, section 7): a Space Packet of 4096 bytes of data in its CPTP packet (4106 bytes) takes 258 cycles,
  19.0 Gbit/s of chunks at 150 MHz. Virtual channel 1 of `ofb0` and its four lanes carry 17.9 Gbit/s already at
  packets of 1024 bytes (OpenFibre user guide, section 7).

`spp0` sends Space Packets of at most 4102 bytes (section "Packet sizes and buffers" of the brief), which the
receive buffer of the far end must hold.

## Clocks and reset

As in spfi_encrypt: `Clk_sys` for all blocks except the lane clock domain of `ofb0`, the OpenCompact channels and the
chunkers included; `Rst` is the asynchronous, high-active reset of the system.

## Lane initialisation

As in spfi_encrypt ([lane initialisation](../spfi_encrypt/README.md#lane-initialisation)): the lane settings of the
OpenFibre reference design for the VCK190, a receiver reset of a lane at every restart and an initialisation timeout of
468750 words (3 ms), which deviates from ECSS-E-ST-50-11C 5.5.2.6b.1 and 5.5.2.7b.1; the bounds of the waits of `pa0`
scaled to the 150 MHz of `Clk_sys`.

## Test software

The hardware test computes the expected compressed image of every test image with the reference model of OpenCompact
(`tools/ocp_model.py` of the submodule `cores/opencompact`) from the generics of the compressor of its channel in the
Composer file, and the images and counters of every channel after lost SDUs with a model of the OpenCompact channel
(`sw/refsys/ocp.py`). It cuts every expected image into chunks of the size of the chunker of its channel and compares
the Space Packets of every channel one by one with these chunks: their bytes, their lengths, and with them the image
boundaries. The aborted images are compared in the same way, with their compressed images from the model of
OpenCompact (`compress_aborted`). The test images are deterministic for a seed and depend on every bit of the sample
address, so that an address fault of a memory of a compressor changes its compressed image.

TC-RS-HW-10 tests the runtime configuration (`sw/refsys/ocptest.py`). Before every image of its plan, channels 0 and 3
get the values of that image. The first COMMIT is followed by COMMITs with one value out of range, which the
compressor must refuse with CFG_ERROR while the configuration stays pending. Every compressed image is compared with
the image of the reference model for the values committed before it; the configuration of each image comes from
`runtime_image_cfg` of OpenCompact (`tools/ocp_configs.py`), which builds the expected images of its own runtime
testbench. Channels 1 and 2 must ignore the writes of the runtime registers.
