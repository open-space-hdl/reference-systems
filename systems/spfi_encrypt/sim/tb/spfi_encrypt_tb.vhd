---------------------------------------------------------------------------------------------------
-- Copyright (c) 2026 by Julian Schneider
-- Authors: Julian Schneider
---------------------------------------------------------------------------------------------------

---------------------------------------------------------------------------------------------------
-- Description
---------------------------------------------------------------------------------------------------
-- System testbench of spfi_encrypt: the generated system and a far-end SpaceFibre node, a second
-- ofb_core with its own physical adapter, joined by their serial lines (lane i to lane i, both
-- directions) and fed by one reference clock and one system clock. The far end stands for the test
-- equipment of the hardware test: it plays stimulus.txt (written by sim/vectors.py from the hardware
-- test), which starts the link through its MIB and then sends packets on its virtual channels and
-- polls registers of the system; it records every packet that it receives, per virtual channel
-- (rx_vc<n>.txt), and the simulated time of its events (events.txt). sim/vectors.py checks the
-- records with the procedure of the hardware test. The physical adapter is the entity
-- openfibre.ofb_pa_gty that the simulation compiles: the AMD GTY adapter with the transceiver model
-- or a pin-compatible model.
--
-- Documentation: systems/spfi_encrypt/sim/README.md

---------------------------------------------------------------------------------------------------
-- Libraries
---------------------------------------------------------------------------------------------------
library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library std;
    use std.textio.all;
    use std.env.all;

library openfibre;

library spfi_encrypt;

library work;
    use work.spfi_encrypt_tb_pkg.all;

---------------------------------------------------------------------------------------------------
-- Entity
---------------------------------------------------------------------------------------------------
entity spfi_encrypt_tb is
    generic (
        -- Far end: the generics of the SpaceFibre port of the system (ofb0 of the Composer file)
        NumLanes_g  : positive := 4;
        NumVc_g     : positive := 2;
        VcInDepth_g : positive := 1024
    );
end entity;

---------------------------------------------------------------------------------------------------
-- Architecture
---------------------------------------------------------------------------------------------------
architecture sim of spfi_encrypt_tb is

    -- Clocks: reference clock of the transceivers 156.25 MHz, system clock 200 MHz
    constant RefPeriod_c : time := 6400 ps;
    constant SysPeriod_c : time := 5 ns;

    -- Files in the working directory of the simulator
    constant StimFile_c  : string := "stimulus.txt";
    constant EventFile_c : string := "events.txt";

    constant BeatBytes_c : positive := 4 * NumLanes_g;
    constant MaxBytes_c  : positive := 16384;    -- longest packet of the stimulus and of the records
    constant HeadBytes_c : positive := 64;       -- first bytes of a received packet that a poll can test
    constant ReadyMax_c  : time     := 2 ms;     -- transmitters of the far end ready

    type IntArray_t is array (natural range <>) of natural;
    type HeadArray_t is array (natural range <>) of std_logic_vector(8 * HeadBytes_c - 1 downto 0);

    -- Clocks and reset
    signal RefClk : std_logic := '0';
    signal ClkSys : std_logic := '0';
    signal Rst    : std_logic := '1';

    -- Serial lines: system to far end and back
    signal SysTxP : std_logic_vector(NumLanes_g - 1 downto 0);
    signal SysTxN : std_logic_vector(NumLanes_g - 1 downto 0);
    signal FarTxP : std_logic_vector(NumLanes_g - 1 downto 0);
    signal FarTxN : std_logic_vector(NumLanes_g - 1 downto 0);

    -- Far end: lane clock and the ready state of the transmitters
    signal FarLaneClk : std_logic;
    signal FarTxReady : std_logic;

    -- Far end: virtual channels
    signal FarSVcTData  : std_logic_vector(32 * NumLanes_g * NumVc_g - 1 downto 0) := (others => '0');
    signal FarSVcTKeep  : std_logic_vector(4 * NumLanes_g * NumVc_g - 1 downto 0)  := (others => '0');
    signal FarSVcTLast  : std_logic_vector(NumVc_g - 1 downto 0)                   := (others => '0');
    signal FarSVcTValid : std_logic_vector(NumVc_g - 1 downto 0)                   := (others => '0');
    signal FarSVcTReady : std_logic_vector(NumVc_g - 1 downto 0);
    signal FarMVcTData  : std_logic_vector(32 * NumLanes_g * NumVc_g - 1 downto 0);
    signal FarMVcTKeep  : std_logic_vector(4 * NumLanes_g * NumVc_g - 1 downto 0);
    signal FarMVcTLast  : std_logic_vector(NumVc_g - 1 downto 0);
    signal FarMVcTUser  : std_logic_vector(NumVc_g - 1 downto 0);
    signal FarMVcTValid : std_logic_vector(NumVc_g - 1 downto 0);

    -- Far end: MIB
    signal FarArAddr  : std_logic_vector(11 downto 0) := (others => '0');
    signal FarArValid : std_logic                     := '0';
    signal FarArReady : std_logic;
    signal FarAwAddr  : std_logic_vector(11 downto 0) := (others => '0');
    signal FarAwValid : std_logic                     := '0';
    signal FarAwReady : std_logic;
    signal FarWData   : std_logic_vector(31 downto 0) := (others => '0');
    signal FarWValid  : std_logic                     := '0';
    signal FarWReady  : std_logic;
    signal FarBValid  : std_logic;
    signal FarBReady  : std_logic                     := '0';
    signal FarRData   : std_logic_vector(31 downto 0);
    signal FarRValid  : std_logic;
    signal FarRReady  : std_logic                     := '0';

    -- Far end: between the core and its physical adapter
    signal PhyTxData       : std_logic_vector(32 * NumLanes_g - 1 downto 0);
    signal PhyTxK          : std_logic_vector(4 * NumLanes_g - 1 downto 0);
    signal PhyRxData       : std_logic_vector(32 * NumLanes_g - 1 downto 0);
    signal PhyRxK          : std_logic_vector(4 * NumLanes_g - 1 downto 0);
    signal PhyRxCodeErr    : std_logic_vector(4 * NumLanes_g - 1 downto 0);
    signal PhyRxDispErr    : std_logic_vector(4 * NumLanes_g - 1 downto 0);
    signal PhyRxValid      : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyTxEnable     : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyRxEnable     : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyCdrEnable    : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyRxInvert     : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyNoSignal     : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyNearLoopback : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyFarLoopback  : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyPrbsTxSel    : std_logic_vector(4 * NumLanes_g - 1 downto 0);
    signal PhyPrbsRxSel    : std_logic_vector(4 * NumLanes_g - 1 downto 0);
    signal PhyPrbsForceErr : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyPrbsCntReset : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyPrbsErr      : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyPrbsLocked   : std_logic_vector(NumLanes_g - 1 downto 0);
    signal PhyAligned      : std_logic_vector(NumLanes_g - 1 downto 0);

    -- Receivers to sequencer: packets received per virtual channel, first bytes and length of the last
    signal RxCount : IntArray_t(0 to NumVc_g - 1) := (others => 0);
    signal RxHead  : HeadArray_t(0 to NumVc_g - 1);
    signal RxLen   : IntArray_t(0 to NumVc_g - 1) := (others => 0);
    signal Done    : std_logic                    := '0';

begin

    -----------------------------------------------------------------------------------------------
    -- Clocks and reset
    -----------------------------------------------------------------------------------------------
    RefClk <= not RefClk after RefPeriod_c / 2;
    ClkSys <= not ClkSys after SysPeriod_c / 2;
    Rst    <= '1', '0' after 1 us;

    -----------------------------------------------------------------------------------------------
    -- System
    -----------------------------------------------------------------------------------------------
    -- The port names of the generated system follow the Composer: <instance>_<port>
    -- vsg_off port_map_002

    i_dut : entity spfi_encrypt.spfi_encrypt
        port map (
            Rst        => Rst,
            Clk_sys    => ClkSys,
            pa0_Gt_TxP => SysTxP,
            pa0_Gt_TxN => SysTxN,
            pa0_Gt_RxP => FarTxP,
            pa0_Gt_RxN => FarTxN,
            pa0_RefClk => RefClk
        );

    -- vsg_on port_map_002

    -----------------------------------------------------------------------------------------------
    -- Far end: physical adapter and SpaceFibre port
    -----------------------------------------------------------------------------------------------
    i_far_pa : entity openfibre.ofb_pa_gty
        generic map (
            NumLanes_g => NumLanes_g
        )
        port map (
            FreeRunClk             => ClkSys,
            Rst                    => Rst,
            RefClk                 => RefClk,
            LaneClk                => FarLaneClk,
            LaneRst                => open,
            Gt_TxP                 => FarTxP,
            Gt_TxN                 => FarTxN,
            Gt_RxP                 => SysTxP,
            Gt_RxN                 => SysTxN,
            PhyTx_Data             => PhyTxData,
            PhyTx_K                => PhyTxK,
            PhyRx_Data             => PhyRxData,
            PhyRx_K                => PhyRxK,
            PhyRx_CodeErr          => PhyRxCodeErr,
            PhyRx_DispErr          => PhyRxDispErr,
            PhyRx_Valid            => PhyRxValid,
            Phy_TxEnable           => PhyTxEnable,
            Phy_RxEnable           => PhyRxEnable,
            Phy_CdrEnable          => PhyCdrEnable,
            Phy_RxInvert           => PhyRxInvert,
            Phy_NoSignal           => PhyNoSignal,
            Phy_SerialNearLoopback => PhyNearLoopback,
            Phy_SerialFarLoopback  => PhyFarLoopback,
            Phy_PrbsTxSel          => PhyPrbsTxSel,
            Phy_PrbsRxSel          => PhyPrbsRxSel,
            Phy_PrbsForceErr       => PhyPrbsForceErr,
            Phy_PrbsCntReset       => PhyPrbsCntReset,
            Phy_PrbsErr            => PhyPrbsErr,
            Phy_PrbsLocked         => PhyPrbsLocked,
            Stat_TxReady           => FarTxReady,
            Stat_RxReady           => open,
            Stat_Aligned           => PhyAligned,
            Stat_RxBufErr          => open,
            Stat_ClkCor            => open
        );

    -- Reset of the core: the reset of the bench; the core holds the link in Near-End Reset until the
    -- transmitters of the adapter are ready (Phy_TxReady from Stat_TxReady)
    i_far_core : entity openfibre.ofb_core
        generic map (
            NumVc_g       => NumVc_g,
            NumLanes_g    => NumLanes_g,
            LaneClkFreq_g => 156.25e6,
            CoreClkFreq_g => 156.25e6,
            VcInDepth_g   => VcInDepth_g
        )
        port map (
            Rst                    => Rst,
            UserClk                => ClkSys,
            CoreClk                => FarLaneClk,
            LaneClk                => FarLaneClk,
            MgmtClk                => ClkSys,
            S_Vc_TData             => FarSVcTData,
            S_Vc_TKeep             => FarSVcTKeep,
            S_Vc_TLast             => FarSVcTLast,
            S_Vc_TUser             => (others => '0'),
            S_Vc_TValid            => FarSVcTValid,
            S_Vc_TReady            => FarSVcTReady,
            M_Vc_TData             => FarMVcTData,
            M_Vc_TKeep             => FarMVcTKeep,
            M_Vc_TLast             => FarMVcTLast,
            M_Vc_TUser             => FarMVcTUser,
            M_Vc_TValid            => FarMVcTValid,
            M_Vc_TReady            => (others => '1'),
            S_Bc_TData             => (others => '0'),
            S_Bc_TUser             => (others => '0'),
            S_Bc_TValid            => '0',
            S_Bc_TReady            => open,
            M_Bc_TData             => open,
            M_Bc_TUser             => open,
            M_Bc_TValid            => open,
            M_Bc_TReady            => '1',
            S_AxiLite_ArAddr       => FarArAddr,
            S_AxiLite_ArValid      => FarArValid,
            S_AxiLite_ArReady      => FarArReady,
            S_AxiLite_AwAddr       => FarAwAddr,
            S_AxiLite_AwValid      => FarAwValid,
            S_AxiLite_AwReady      => FarAwReady,
            S_AxiLite_WData        => FarWData,
            S_AxiLite_WStrb        => "1111",
            S_AxiLite_WValid       => FarWValid,
            S_AxiLite_WReady       => FarWReady,
            S_AxiLite_BResp        => open,
            S_AxiLite_BValid       => FarBValid,
            S_AxiLite_BReady       => FarBReady,
            S_AxiLite_RData        => FarRData,
            S_AxiLite_RResp        => open,
            S_AxiLite_RValid       => FarRValid,
            S_AxiLite_RReady       => FarRReady,
            Irq                    => open,
            PhyTx_Data             => PhyTxData,
            PhyTx_K                => PhyTxK,
            PhyRx_Data             => PhyRxData,
            PhyRx_K                => PhyRxK,
            PhyRx_CodeErr          => PhyRxCodeErr,
            PhyRx_DispErr          => PhyRxDispErr,
            PhyRx_Valid            => PhyRxValid,
            Phy_TxEnable           => PhyTxEnable,
            Phy_RxEnable           => PhyRxEnable,
            Phy_CdrEnable          => PhyCdrEnable,
            Phy_RxInvert           => PhyRxInvert,
            Phy_NoSignal           => PhyNoSignal,
            Phy_TxReady            => FarTxReady,
            Phy_BitSync            => PhyAligned,
            Phy_SerialNearLoopback => PhyNearLoopback,
            Phy_SerialFarLoopback  => PhyFarLoopback,
            Phy_PrbsTxSel          => PhyPrbsTxSel,
            Phy_PrbsRxSel          => PhyPrbsRxSel,
            Phy_PrbsForceErr       => PhyPrbsForceErr,
            Phy_PrbsCntReset       => PhyPrbsCntReset,
            Phy_PrbsErr            => PhyPrbsErr,
            Phy_PrbsLocked         => PhyPrbsLocked
        );

    -----------------------------------------------------------------------------------------------
    -- Receivers: one per virtual channel of the far end, always ready
    -----------------------------------------------------------------------------------------------
    g_rx : for Vc in 0 to NumVc_g - 1 generate

        p_rx : process is
            file RxFile : text open write_mode is "rx_vc" & integer'image(Vc) & ".txt";

            variable Line_v  : line;
            variable Bytes_v : ByteArray_t(0 to MaxBytes_c - 1);
            variable Len_v   : natural := 0;
            variable Count_v : natural := 0;
            variable Head_v  : std_logic_vector(8 * HeadBytes_c - 1 downto 0);
            variable Lo_v    : natural;
        begin

            loop
                wait until rising_edge(ClkSys) or Done = '1';

                if Done = '1' then
                    file_close(RxFile);
                    wait;
                end if;

                if FarMVcTValid(Vc) = '1' then

                    for I in 0 to BeatBytes_c - 1 loop
                        if FarMVcTKeep(BeatBytes_c * Vc + I) = '1' then
                            assert Len_v < MaxBytes_c
                                report "spfi_encrypt_tb: received packet longer than " & integer'image(MaxBytes_c) & " bytes"
                                severity failure;
                            Lo_v           := 32 * NumLanes_g * Vc + 8 * I;
                            Bytes_v(Len_v) := FarMVcTData(Lo_v + 7 downto Lo_v);
                            Len_v          := Len_v + 1;
                        end if;
                    end loop;

                    if FarMVcTLast(Vc) = '1' then
                        -- <ns> <bytes> <EEP> <hexadecimal bytes>
                        write(Line_v, integer'image(now / 1 ns) & " " & integer'image(Len_v) & " ");
                        if FarMVcTUser(Vc) = '1' then
                            write(Line_v, string'("1 "));
                        else
                            write(Line_v, string'("0 "));
                        end if;
                        write(Line_v, toHex(Bytes_v, Len_v));
                        writeline(RxFile, Line_v);
                        Head_v := (others => '0');

                        for I in 0 to HeadBytes_c - 1 loop
                            if I < Len_v then
                                Head_v(8 * I + 7 downto 8 * I) := Bytes_v(I);
                            end if;
                        end loop;

                        Count_v     := Count_v + 1;
                        RxHead(Vc)  <= Head_v;
                        RxLen(Vc)   <= Len_v;
                        RxCount(Vc) <= Count_v;
                        Len_v       := 0;
                    end if;
                end if;
            end loop;

        end process;

    end generate;

    -----------------------------------------------------------------------------------------------
    -- Sequencer: plays the stimulus file
    -----------------------------------------------------------------------------------------------
    p_seq : process is
        file StimFile  : text open read_mode is StimFile_c;
        file EventFile : text open write_mode is EventFile_c;

        variable Line_v     : line;
        variable Word_v     : string(1 to 16);
        variable WordLen_v  : natural;
        variable Op_v       : integer                      := -1;
        variable Vc_v       : natural;
        variable Count_v    : natural;
        variable Limit_v    : natural;
        variable Offset_v   : natural;
        variable Addr_v     : std_logic_vector(31 downto 0);
        variable Data_v     : std_logic_vector(31 downto 0);
        variable Mask_v     : std_logic_vector(31 downto 0);
        variable Value_v    : std_logic_vector(31 downto 0);
        variable Tries_v    : natural;
        variable Deadline_v : time;
        variable Pkt_v      : ByteArray_t(0 to MaxBytes_c - 1);
        variable PktLen_v   : natural;
        variable Expect_v   : IntArray_t(0 to NumVc_g - 1) := (others => 0);
        variable Word32_v   : std_logic_vector(31 downto 0);
        variable Stop_v     : boolean                      := false;

        -- An event: a line of the event file, and a note in the log for the runner
        procedure logEvent (
            Kind : string;
            Info : string := "") is
            variable L_v : line;
        begin
            write(L_v, integer'image(now / 1 ns) & " " & Kind & " " & integer'image(Op_v));
            if Info'length > 0 then
                write(L_v, " " & Info);
            end if;
            report "event " & L_v.all severity note;
            writeline(EventFile, L_v);
        end procedure;

        procedure mibWrite (
            Addr : std_logic_vector(31 downto 0);
            Data : std_logic_vector(31 downto 0)) is
            variable AwDone_v : boolean := false;
            variable WDone_v  : boolean := false;
        begin
            FarAwAddr  <= Addr(11 downto 0);
            FarAwValid <= '1';
            FarWData   <= Data;
            FarWValid  <= '1';
            FarBReady  <= '1';

            while not (AwDone_v and WDone_v) loop
                wait until rising_edge(ClkSys);
                if not AwDone_v and FarAwReady = '1' then
                    AwDone_v   := true;
                    FarAwValid <= '0';
                end if;
                if not WDone_v and FarWReady = '1' then
                    WDone_v   := true;
                    FarWValid <= '0';
                end if;
            end loop;

            loop
                exit when FarBValid = '1';
                wait until rising_edge(ClkSys);
            end loop;

            wait until rising_edge(ClkSys);
            FarBReady <= '0';
        end procedure;

        procedure mibRead (
            Addr : std_logic_vector(31 downto 0);
            Data : out std_logic_vector(31 downto 0)) is
            variable ArDone_v : boolean := false;
        begin
            FarArAddr  <= Addr(11 downto 0);
            FarArValid <= '1';
            FarRReady  <= '1';

            loop
                wait until rising_edge(ClkSys);
                if not ArDone_v and FarArReady = '1' then
                    ArDone_v   := true;
                    FarArValid <= '0';
                end if;
                if FarRValid = '1' then
                    Data := FarRData;
                    exit;
                end if;
            end loop;

            FarRReady <= '0';
        end procedure;

        -- One packet on a virtual channel of the far end: byte 0 of a beat in bits 7:0
        procedure vcSend (
            Vc  : natural;
            Pkt : ByteArray_t;
            Len : natural) is
            variable Data_v : std_logic_vector(32 * NumLanes_g - 1 downto 0);
            variable Keep_v : std_logic_vector(BeatBytes_c - 1 downto 0);
            variable Lo_v   : natural;
        begin
            Lo_v := 32 * NumLanes_g * Vc;

            for Beat in 0 to (Len + BeatBytes_c - 1) / BeatBytes_c - 1 loop

                for I in 0 to BeatBytes_c - 1 loop
                    if Beat * BeatBytes_c + I < Len then
                        Data_v(8 * I + 7 downto 8 * I) := Pkt(Beat * BeatBytes_c + I);
                        Keep_v(I)                      := '1';
                    else
                        Data_v(8 * I + 7 downto 8 * I) := (others => '0');
                        Keep_v(I)                      := '0';
                    end if;
                end loop;

                FarSVcTData(Lo_v + 32 * NumLanes_g - 1 downto Lo_v)             <= Data_v;
                FarSVcTKeep(BeatBytes_c * (Vc + 1) - 1 downto BeatBytes_c * Vc) <= Keep_v;
                FarSVcTValid(Vc)                                                <= '1';
                if (Beat + 1) * BeatBytes_c >= Len then
                    FarSVcTLast(Vc) <= '1';
                else
                    FarSVcTLast(Vc) <= '0';
                end if;

                loop
                    wait until rising_edge(ClkSys);
                    exit when FarSVcTReady(Vc) = '1';
                end loop;

            end loop;

            FarSVcTValid(Vc) <= '0';
            FarSVcTLast(Vc)  <= '0';
        end procedure;

        -- Waits until the receiver of a virtual channel has the awaited number of packets or the time is over
        procedure awaitPackets (
            Vc       : natural;
            Deadline : time;
            Ok       : out boolean) is
        begin
            if RxCount(Vc) < Expect_v(Vc) and now < Deadline then
                wait until RxCount(Vc) >= Expect_v(Vc) for Deadline - now;
            end if;
            Ok := RxCount(Vc) >= Expect_v(Vc);
        end procedure;

        variable Ok_v : boolean;
    begin
        -- The far end starts when the transmitters of its adapter are ready
        wait until FarTxReady = '1' for ReadyMax_c;
        if FarTxReady /= '1' then
            logEvent("timeout", "transmitters of the far end not ready");
            Stop_v := true;
        else

            for I in 1 to 10 loop
                wait until rising_edge(ClkSys);
            end loop;

            logEvent("ready");
        end if;

        while not Stop_v and not endfile(StimFile) loop
            readline(StimFile, Line_v);
            readWord(Line_v, Word_v, WordLen_v);

            if WordLen_v = 0 or Word_v(1) = '#' then
                next;
            end if;

            Op_v := Op_v + 1;

            if isWord(Word_v, WordLen_v, "mark") then
                readWord(Line_v, Word_v, WordLen_v);
                logEvent("mark", Word_v(1 to WordLen_v));

            elsif isWord(Word_v, WordLen_v, "mibw") then
                readHex32(Line_v, Addr_v);
                readHex32(Line_v, Data_v);
                mibWrite(Addr_v, Data_v);

            elsif isWord(Word_v, WordLen_v, "mibp") then
                readHex32(Line_v, Addr_v);
                readHex32(Line_v, Mask_v);
                readHex32(Line_v, Value_v);
                read(Line_v, Limit_v);
                Deadline_v := now + Limit_v * 1 ns;
                Tries_v    := 0;

                loop
                    mibRead(Addr_v, Data_v);
                    Tries_v := Tries_v + 1;
                    if (Data_v and Mask_v) = Value_v then
                        logEvent("mibp", integer'image(Tries_v));
                        exit;
                    end if;
                    if now >= Deadline_v then
                        logEvent("timeout", "0x" & toHex((Data_v(31 downto 24), Data_v(23 downto 16),
                                                        Data_v(15 downto 8), Data_v(7 downto 0)), 4));
                        Stop_v := true;
                        exit;
                    end if;
                    wait for 1 us;
                end loop;

            elsif isWord(Word_v, WordLen_v, "send") then
                read(Line_v, Vc_v);
                read(Line_v, PktLen_v);
                readBytes(Line_v, PktLen_v, Pkt_v);
                vcSend(Vc_v, Pkt_v, PktLen_v);

            elsif isWord(Word_v, WordLen_v, "recv") then
                read(Line_v, Vc_v);
                read(Line_v, Count_v);
                read(Line_v, Limit_v);
                Expect_v(Vc_v) := Expect_v(Vc_v) + Count_v;
                awaitPackets(Vc_v, now + Limit_v * 1 ns, Ok_v);
                if not Ok_v then
                    logEvent("timeout", integer'image(RxCount(Vc_v)) & " of " & integer'image(Expect_v(Vc_v)));
                    Stop_v := true;
                end if;

            elsif isWord(Word_v, WordLen_v, "poll") then
                -- poll <vc> <limit ns> <byte offset> <mask> <value> <bytes> <packet>: sends the packet until the
                -- little-endian word at the offset of the reply has the value under the mask
                read(Line_v, Vc_v);
                read(Line_v, Limit_v);
                read(Line_v, Offset_v);
                readHex32(Line_v, Mask_v);
                readHex32(Line_v, Value_v);
                read(Line_v, PktLen_v);
                readBytes(Line_v, PktLen_v, Pkt_v);
                Deadline_v := now + Limit_v * 1 ns;
                Tries_v    := 0;

                loop
                    vcSend(Vc_v, Pkt_v, PktLen_v);
                    Tries_v        := Tries_v + 1;
                    Expect_v(Vc_v) := Expect_v(Vc_v) + 1;
                    awaitPackets(Vc_v, Deadline_v, Ok_v);
                    if not Ok_v then
                        logEvent("timeout", integer'image(Tries_v) & " tries");
                        Stop_v := true;
                        exit;
                    end if;
                    -- a reply without the word ends the poll; the check reports it
                    if RxLen(Vc_v) < Offset_v + 4 then
                        logEvent("poll", integer'image(Tries_v));
                        exit;
                    end if;
                    Word32_v := RxHead(Vc_v)(8 * Offset_v + 31 downto 8 * Offset_v);
                    if (Word32_v and Mask_v) = Value_v then
                        logEvent("poll", integer'image(Tries_v));
                        exit;
                    end if;
                    if now >= Deadline_v then
                        logEvent("timeout", integer'image(Tries_v) & " tries");
                        Stop_v := true;
                        exit;
                    end if;
                end loop;

            elsif isWord(Word_v, WordLen_v, "end") then
                logEvent("end");
                Stop_v := true;

            else
                report "spfi_encrypt_tb: unknown operation " & Word_v(1 to WordLen_v)
                    severity failure;
            end if;

            deallocate(Line_v);
        end loop;

        -- The receivers close their files, then the simulation ends
        Done <= '1';
        wait for 1 ns;
        file_close(EventFile);
        report "Simulation done" severity note;
        finish;
    end process;

end architecture;
