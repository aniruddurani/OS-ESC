/* SOEM's standard start-up against the ESC on the DE2-115: ecx_config_init reads the identity
 * and categories from SII, ecx_config_map_group maps the process data, the slave goes to OP,
 * and cyclic process data runs. The identity, name and sizes are those of model/sii.py.
 *
 * The ESC has no local side, so nothing but the master changes process data: this program
 * writes the input area itself, and reads the output area back from RAM.
 *
 *   soem_config <ifname> [cycles=10000]     ifname: \Device\NPF_{GUID}
 *
 * Prints one JSON object: {"checks": {name: bool}, "pass": bool, "cycles": n, "cycle_us": {...}}.
 * Run right after the board's soft reset (board.mk's bsoem does that). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#include "soem/soem.h"

#define VENDOR    0u
#define PRODUCT   1u
#define REVISION  1u
#define NAME      "OS-ESC"
#define OUT_ADDR  0x1000
#define IN_ADDR   0x1080
#define PD_BYTES  8
#define PD_TIMEOUT (10 * EC_TIMEOUTRET)   /* 20 ms: Windows can hold a reply past 2 ms; a lost frame still fails */

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static const char *tf(int v)
{
    return v ? "true" : "false";
}

int main(int argc, char **argv)
{
    int cycles = argc > 2 ? atoi(argv[2]) : 10000, done = 0;
    int found = 0, identity = 0, name = 0, mapping = 0, safe_op = 0, op = 0;
    int inputs_seen = 0, cycles_ok = 0, outputs_ram = 0, back_to_init = 0;
    double *us = calloc((size_t)(cycles > 0 ? cycles : 1), sizeof(double));
    static ecx_contextt ctx;
    static uint8 iomap[256];
    uint8 last[PD_BYTES] = {0}, in_pattern[PD_BYTES];
    LARGE_INTEGER f, t0, t1;

    if (argc < 2 || !us) {
        fprintf(stderr, "usage: soem_config <ifname> [cycles]\n");
        return 2;
    }
    if (!ecx_init(&ctx, argv[1])) {
        fprintf(stderr, "ecx_init(%s) failed\n", argv[1]);
        return 2;
    }

    found = ecx_config_init(&ctx) == 1;
    if (found) {
        ec_slavet *s = &ctx.slavelist[1];
        identity = s->eep_man == VENDOR && s->eep_id == PRODUCT && s->eep_rev == REVISION;
        name = strcmp(s->name, NAME) == 0;

        ecx_config_map_group(&ctx, iomap, 0);
        mapping = s->Obytes == PD_BYTES && s->Ibytes == PD_BYTES;
        safe_op = ecx_statecheck(&ctx, 0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE) == EC_STATE_SAFE_OP;

        for (int i = 0; i < PD_BYTES; i++)
            in_pattern[i] = (uint8)(0xA0 + i);
        ecx_FPWR(&ctx.port, s->configadr, IN_ADDR, PD_BYTES, in_pattern, EC_TIMEOUTRET3);

        ctx.slavelist[0].state = EC_STATE_OPERATIONAL;
        ecx_writestate(&ctx, 0);
        op = ecx_statecheck(&ctx, 0, EC_STATE_OPERATIONAL, EC_TIMEOUTSTATE) == EC_STATE_OPERATIONAL;

        if (mapping && op) {
            inputs_seen = cycles_ok = 1;
            QueryPerformanceFrequency(&f);
            for (; done < cycles; done++) {
                for (int i = 0; i < PD_BYTES; i++)
                    last[i] = s->outputs[i] = (uint8)(done * PD_BYTES + i);
                QueryPerformanceCounter(&t0);
                ecx_send_processdata(&ctx);
                int wkc = ecx_receive_processdata(&ctx, PD_TIMEOUT);
                QueryPerformanceCounter(&t1);
                us[done] = (double)(t1.QuadPart - t0.QuadPart) * 1e6 / (double)f.QuadPart;
                if (wkc != 3) {
                    fprintf(stderr, "cycle %d: wkc %d\n", done, wkc);
                    cycles_ok = 0;
                    break;
                }
                if (memcmp(s->inputs, in_pattern, PD_BYTES) != 0)
                    inputs_seen = 0;
            }
            uint8 back[PD_BYTES] = {0};
            outputs_ram = cycles_ok &&
                          ecx_FPRD(&ctx.port, s->configadr, OUT_ADDR, PD_BYTES, back, EC_TIMEOUTRET3) == 1 &&
                          memcmp(back, last, PD_BYTES) == 0;
        }

        ctx.slavelist[0].state = EC_STATE_INIT;
        ecx_writestate(&ctx, 0);
        back_to_init = ecx_statecheck(&ctx, 0, EC_STATE_INIT, EC_TIMEOUTSTATE) == EC_STATE_INIT;
    }
    ecx_close(&ctx);

    qsort(us, (size_t)done, sizeof(double), cmp_double);
    int all = found && identity && name && mapping && safe_op && op && inputs_seen && cycles_ok &&
              outputs_ram && back_to_init;
    printf("{\"checks\": {\"found_one\": %s, \"identity\": %s, \"name\": %s, \"mapping\": %s, "
           "\"safe_op\": %s, \"op\": %s, \"inputs_seen\": %s, \"cycles_ok\": %s, "
           "\"outputs_ram\": %s, \"back_to_init\": %s}, \"pass\": %s, "
           "\"cycle_us\": {\"min\": %.1f, \"median\": %.1f, \"max\": %.1f}, \"cycles\": %d}\n",
           tf(found), tf(identity), tf(name), tf(mapping), tf(safe_op), tf(op), tf(inputs_seen),
           tf(cycles_ok), tf(outputs_ram), tf(back_to_init), tf(all),
           done ? us[0] : 0.0, done ? us[done / 2] : 0.0, done ? us[done - 1] : 0.0, done);
    free(us);
    return all ? 0 : 1;
}
