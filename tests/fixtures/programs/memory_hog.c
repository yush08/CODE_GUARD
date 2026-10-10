#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* Demo: memory-heavy program. Keeps allocating 16 MB blocks until the
 * configured job memory limit stops the allocations (kernel-enforced). */
int main(void) {
    size_t total = 0;
    for (;;) {
        void *p = malloc(16 * 1024 * 1024);
        if (p == NULL) {
            printf("malloc stopped after %zu MB\n", total / (1024 * 1024));
            return 2;
        }
        memset(p, 1, 16 * 1024 * 1024); /* touch the pages */
        total += 16 * 1024 * 1024;
        if (total > (size_t)2048 * 1024 * 1024) {
            printf("unexpectedly reached %zu MB without hitting the limit\n", total / (1024 * 1024));
            return 3;
        }
    }
}
