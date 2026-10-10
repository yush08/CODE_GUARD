#include <stdio.h>
/* Demo: output storm — prints until the configured output limit terminates
 * the program. */
int main(void) {
    long long i;
    for (i = 0; i < 100000000LL; i++) {
        printf("line %lld some padding text to make the output grow fast\n", i);
    }
    return 0;
}
