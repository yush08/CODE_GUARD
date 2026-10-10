#include <windows.h>
#include <stdio.h>
/* Demo: wall-clock sleeper (no CPU burn). Only the wall-clock deadline
 * can stop this program; CPU-time accounting stays near zero. */
int main(void) {
    Sleep(120000);
    printf("slept\n");
    return 0;
}
