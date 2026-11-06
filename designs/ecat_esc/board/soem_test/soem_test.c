/* SOEM low-level test against the ESC on the DE2-115: addressing, FMMU and SyncManager
 * configuration and cyclic LRW, through SOEM's datagram calls (ecx_*) only (the ESC has no
 * AL state machine or SII EEPROM, so ecx_config_init is not used).
 *
 *   soem_test <ifname> [cycles=10000]     ifname: \Device\NPF_{GUID}
 *
 * Prints one JSON object: {"checks": {name: bool}, "pass": bool, "cycle_us": {...}}.
 * Run right after the board's soft reset (board.mk's bsoem does that). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#include "soem/soem.h"

#define TIMEOUT   (10 * EC_TIMEOUTRET)   /* 20 ms: Windows can hold a reply past 2 ms; a lost frame still fails */
#define STATION   0x1001
#define LOG_ADDR  0x00010000u
#define PD_ADDR   0x1000
#define PD_LEN    8

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

int main(int argc, char **argv)
{
    int cycles = argc > 2 ? atoi(argv[2]) : 10000;
    int brd = 0, station = 0, fmmu = 0, sm = 0, lrw = 0;
    double *us = calloc((size_t)(cycles > 0 ? cycles : 1), sizeof(double));
    LARGE_INTEGER f, t0, t1;
    static ecx_contextt ctx;
    ecx_portt *port = &ctx.port;

    if (argc < 2 || !us) {
        fprintf(stderr, "usage: soem_test <ifname> [cycles]\n");
        return 2;
    }
    if (!ecx_init(&ctx, argv[1])) {
        fprintf(stderr, "ecx_init(%s) failed\n", argv[1]);
        return 2;
    }

    /* 1. One slave answers a broadcast read. */
    {
        uint16 w = 0;
        brd = ecx_BRD(port, 0x0000, 0x0000, sizeof w, &w, TIMEOUT) == 1;
    }

    /* 2. Set the configured station address by position, read it back by address. */
    {
        uint16 a = htoes(STATION), r = 0;
        station = ecx_APWR(port, 0x0000, ECT_REG_STADR, sizeof a, &a, TIMEOUT) == 1 &&
                  ecx_FPRD(port, STATION, ECT_REG_STADR, sizeof r, &r, TIMEOUT) == 1 &&
                  etohs(r) == STATION;
    }

    /* 3. FMMU0: logical LOG_ADDR, PD_LEN bytes, bits 0..7, onto physical PD_ADDR, read and
     *    write, enabled. Bytes 0..12 are writable; compare those. */
    {
        uint8 w[16] = {0}, r[16] = {0};
        uint32 la = htoel(LOG_ADDR);
        uint16 len = htoes(PD_LEN), pa = htoes(PD_ADDR);
        memcpy(w + 0, &la, 4);
        memcpy(w + 4, &len, 2);
        w[6] = 0;                      /* logical start bit */
        w[7] = 7;                      /* logical end bit */
        memcpy(w + 8, &pa, 2);
        w[10] = 0;                     /* physical start bit */
        w[11] = 3;                     /* type: read and write */
        w[12] = 1;                     /* activate */
        fmmu = ecx_FPWR(port, STATION, ECT_REG_FMMU0, sizeof w, w, TIMEOUT) == 1 &&
               ecx_FPRD(port, STATION, ECT_REG_FMMU0, sizeof r, r, TIMEOUT) == 1 &&
               memcmp(w, r, 13) == 0;
    }

    /* 4. SM2 = 0x1000/4/0x64 and SM3 = 0x1004/4/0x20, activated. Status (+5) and PDI
     *    control (+7) are read-only; compare the rest. */
    {
        uint8 w[16] = {0}, r[16] = {0};
        uint16 s2 = htoes(PD_ADDR), s3 = htoes(PD_ADDR + 4), l = htoes(4);
        memcpy(w + 0, &s2, 2); memcpy(w + 2, &l, 2); w[4] = 0x64; w[6] = 1;
        memcpy(w + 8, &s3, 2); memcpy(w + 10, &l, 2); w[12] = 0x20; w[14] = 1;
        sm = ecx_FPWR(port, STATION, ECT_REG_SM2, sizeof w, w, TIMEOUT) == 1 &&
             ecx_FPRD(port, STATION, ECT_REG_SM2, sizeof r, r, TIMEOUT) == 1;
        for (int i = 0; sm && i < 16; i++)
            if (i % 8 != 5 && i % 8 != 7 && w[i] != r[i])
                sm = 0;
    }

    /* 5. Cyclic LRW: each cycle writes an incrementing pattern and reads back what the
     *    previous cycle wrote (the ESC reads each byte before writing it). Working counter
     *    3 = read and write both on this slave. The process data starts zeroed. */
    {
        uint8 prev[PD_LEN] = {0}, buf[PD_LEN];
        int ok = ecx_FPWR(port, STATION, PD_ADDR, PD_LEN, prev, TIMEOUT) == 1;
        QueryPerformanceFrequency(&f);
        for (int c = 0; ok && c < cycles; c++) {
            uint8 next[PD_LEN];
            for (int i = 0; i < PD_LEN; i++)
                next[i] = (uint8)(c * PD_LEN + i);
            memcpy(buf, next, PD_LEN);
            QueryPerformanceCounter(&t0);
            int wkc = ecx_LRW(port, LOG_ADDR, PD_LEN, buf, TIMEOUT);
            QueryPerformanceCounter(&t1);
            us[c] = (double)(t1.QuadPart - t0.QuadPart) * 1e6 / (double)f.QuadPart;
            if (wkc != 3 || memcmp(buf, prev, PD_LEN) != 0) {
                fprintf(stderr, "cycle %d: wkc %d\n", c, wkc);
                ok = 0;
            }
            memcpy(prev, next, PD_LEN);
        }
        lrw = ok;
    }
    ecx_close(&ctx);

    qsort(us, (size_t)cycles, sizeof(double), cmp_double);
    int all = brd && station && fmmu && sm && lrw;
    printf("{\"checks\": {\"brd_count\": %s, \"station_address\": %s, \"fmmu_config\": %s, "
           "\"sm_config\": %s, \"lrw_cycles\": %s}, \"pass\": %s, "
           "\"cycle_us\": {\"min\": %.1f, \"median\": %.1f, \"max\": %.1f}, \"cycles\": %d}\n",
           brd ? "true" : "false", station ? "true" : "false", fmmu ? "true" : "false",
           sm ? "true" : "false", lrw ? "true" : "false", all ? "true" : "false",
           cycles ? us[0] : 0.0, cycles ? us[cycles / 2] : 0.0, cycles ? us[cycles - 1] : 0.0,
           cycles);
    free(us);
    return all ? 0 : 1;
}
