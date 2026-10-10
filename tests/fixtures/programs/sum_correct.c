#include <stdio.h>
/* Demo: correct solution. Reads two integers, prints their sum. */
int main(void) {
    long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) { return 1; }
    printf("%lld\n", a + b);
    return 0;
}
