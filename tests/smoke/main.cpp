#include "Vtop.h"
#include "verilated.h"
#include "verilated_cov.h"
#include "verilated_vcd_c.h"
#include <iostream>

int main(int argc, char** argv) {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    context.traceEverOn(true);
    Vtop model{&context};
    VerilatedVcdC trace;
    model.trace(&trace, 5);
    trace.open("trace.vcd");
    for (unsigned cycle = 0; cycle < 9; ++cycle) {
        model.rst = cycle == 0;
        for (unsigned edge = 0; edge < 2; ++edge) {
            model.clk = edge;
            model.eval();
            trace.dump(context.time());
            context.timeInc(1);
        }
    }
    model.final();
    trace.close();
    context.coveragep()->write("coverage.dat");
    if (model.count != 8) return 1;
    std::cout << "simulation passed\n";
    return 0;
}
