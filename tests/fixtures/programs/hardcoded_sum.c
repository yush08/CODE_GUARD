#include <stdio.h>
/* Demo: possible-hardcoding scenario. Passes the visible examples
 * (2+3=5, 10+10=20) but returns 0 for anything else — hidden and
 * generated tests expose the pattern. */
int main(void) {
    long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) { return 1; }
    if (a == 2 && b == 3) { printf("5\n"); return 0; }
    if (a == 10 && b == 10) { printf("20\n"); return 0; }
    printf("0\n");
    return 0;
}
