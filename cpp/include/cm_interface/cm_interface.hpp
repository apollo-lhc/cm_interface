// C++ access to the cm_interface Python package via the embedded CPython
// interpreter. The Python package stays the single implementation of the
// wire protocol, register maps, and UART locking; this layer only calls it.
//
// Requirements: the cm_interface package (and pyserial) importable by the
// embedded interpreter -- installed by the RPM, or found via PYTHONPATH.
#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>

namespace cm {

// A Python exception raised inside cm_interface. python_type() is the
// exception class name (e.g. "UARTLockTimeout", "RegisterAccessError",
// "KeyError" for an unknown Firefly location).
class Error : public std::runtime_error {
 public:
  Error(const std::string& python_type, const std::string& message)
      : std::runtime_error(python_type + ": " + message),
        python_type_(python_type) {}
  const std::string& python_type() const { return python_type_; }

 private:
  std::string python_type_;
};

// Wraps cm_interface.registry.Registry. Registry is a process-wide
// singleton in Python: the first Interface constructed fixes setup and
// dev_path, and later instances share it regardless of their arguments.
//
// Thread-safe: every call takes the GIL, and the Python UART lock
// serializes port access against other threads and processes.
//
// The interpreter is started on first use and never finalized. If the host
// program already initialized Python, that interpreter is used.
class Interface {
 public:
  explicit Interface(const std::string& setup = "tf",
                     const std::string& dev_path = "/dev/ttyUL4");
  ~Interface();
  Interface(const Interface&) = delete;
  Interface& operator=(const Interface&) = delete;

  // Raw common status byte (Firefly register 0x02) of the Firefly at
  // `location`, a Registry key such as "F1_2_Tx".
  std::uint8_t firefly_status(const std::string& location);

 private:
  void* registry_;  // PyObject*, kept opaque so callers need no Python.h
};

}  // namespace cm
