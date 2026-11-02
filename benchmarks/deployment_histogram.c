/* Deployment-only workload: identical kernel across selectable input distributions. */
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t next_u64(uint64_t *state) {
    uint64_t z = (*state += UINT64_C(0x9e3779b97f4a7c15));
    z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
    return z ^ (z >> 31);
}
static uint64_t hash_u64(uint64_t hash, uint64_t value) {
    for (unsigned shift = 0; shift < 64; shift += 8) {
        hash ^= (value >> shift) & UINT64_C(255);
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}
static int parse_u64(const char *text, uint64_t *out) {
    char *end = NULL;
    errno = 0;
    if (!text[0] || text[0] == '-') return 0;
    *out = strtoull(text, &end, 10);
    return !errno && !*end;
}
int main(int argc, char **argv) {
    uint64_t size, seed, repeat;
    if (argc != 9 || strcmp(argv[1], "--size") || strcmp(argv[3], "--seed") ||
        strcmp(argv[5], "--repeat") || strcmp(argv[7], "--distribution") ||
        !parse_u64(argv[2], &size) || !parse_u64(argv[4], &seed) || !parse_u64(argv[6], &repeat) ||
        !size || size > UINT64_C(16000000) || !repeat || repeat > 100) return 2;
    int distribution;
    if (!strcmp(argv[8], "uniform")) distribution = 0;
    else if (!strcmp(argv[8], "skewed")) distribution = 1;
    else if (!strcmp(argv[8], "alternating")) distribution = 2;
    else return 2;
    uint32_t *values = malloc((size_t)size * sizeof(*values));
    if (!values) return 3;
    uint64_t state = seed;
    for (uint64_t i = 0; i < size; ++i) {
        uint32_t raw = (uint32_t)next_u64(&state);
        if (distribution == 1) raw = (raw & 7u) ? (raw | 1u) : (raw & ~1u);
        else if (distribution == 2) raw = (raw & ~1u) | (uint32_t)(i & 1u);
        values[i] = raw;
    }
    uint64_t hash = UINT64_C(14695981039346656037);
    for (uint64_t r = 0; r < repeat; ++r) {
        uint64_t bins[256] = {0};
        values[r % size] ^= (uint32_t)(r + 1);
        for (uint64_t i = 0; i < size; ++i) {
            uint32_t value = values[i], bin;
            if (value & 1u) bin = (value >> 8) & 255u;
            else if (value & 2u) bin = (value >> 16) & 255u;
            else bin = value & 255u;
            ++bins[bin];
        }
        for (unsigned i = 0; i < 256; ++i) hash = hash_u64(hash, bins[i]);
    }
    printf("checksum:%016" PRIx64 "\n", hash);
    free(values);
    return 0;
}

