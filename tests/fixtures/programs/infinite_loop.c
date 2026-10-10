/* Demo: infinite loop (busy wait) — must be stopped by the wall-clock
 * deadline and CPU-time enforcement. Bounded by the judge, never left running. */
int main(void) {
    volatile unsigned long long x = 0;
    for (;;) { x++; }
    return 0;
}
