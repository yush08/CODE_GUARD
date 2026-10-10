#include <stdio.h>
/* Demo: crashes with a null-pointer dereference -> RUNTIME_ERROR. */
int main(void) {
    int *p = 0;
    printf("about to crash\n");
    fflush(stdout);
    *p = 42;
    return 0;
}
