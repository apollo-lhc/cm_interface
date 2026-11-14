// Print the raw status byte (register 0x02) of one Firefly.
//   ff_status_byte LOCATION [SETUP [DEV_PATH]]
#include <cstdio>
#include <string>

#include "cm_interface/cm_interface.hpp"

int main(int argc, char** argv) {
  if (argc < 2 || argc > 4) {
    std::fprintf(stderr, "usage: %s LOCATION [SETUP [DEV_PATH]]\n", argv[0]);
    return 2;
  }
  const std::string setup = argc > 2 ? argv[2] : "tf";
  const std::string dev_path = argc > 3 ? argv[3] : "/dev/ttyUL4";
  try {
    cm::Interface cm(setup, dev_path);
    unsigned status = cm.firefly_status(argv[1]);
    std::printf("%s status 0x%02X\n", argv[1], status);
    return 0;
  } catch (const cm::Error& e) {
    std::fprintf(stderr, "error: %s\n", e.what());
    return 1;
  }
}
