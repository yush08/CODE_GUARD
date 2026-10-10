#include <iostream>
/* Demo: correct C++ solution for the same sum problem. */
int main() {
    long long a, b;
    if (!(std::cin >> a >> b)) { return 1; }
    std::cout << (a + b) << std::endl;
    return 0;
}
