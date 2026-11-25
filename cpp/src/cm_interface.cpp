// Raw CPython C API (3.6+): no binding library dependency.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "cm_interface/cm_interface.hpp"

#include <mutex>

namespace cm {
namespace {

// Owns one strong reference.
class PyRef {
 public:
  explicit PyRef(PyObject* p = nullptr) : p_(p) {}
  ~PyRef() { Py_XDECREF(p_); }
  PyRef(const PyRef&) = delete;
  PyRef& operator=(const PyRef&) = delete;
  PyObject* get() const { return p_; }
  PyObject* release() {
    PyObject* p = p_;
    p_ = nullptr;
    return p;
  }
  explicit operator bool() const { return p_ != nullptr; }

 private:
  PyObject* p_;
};

class Gil {
 public:
  Gil() : state_(PyGILState_Ensure()) {}
  ~Gil() { PyGILState_Release(state_); }
  Gil(const Gil&) = delete;
  Gil& operator=(const Gil&) = delete;

 private:
  PyGILState_STATE state_;
};

std::string to_string(PyObject* obj) {
  if (obj == nullptr) return "";
  PyRef s(PyObject_Str(obj));
  if (!s) {
    PyErr_Clear();
    return "<unprintable>";
  }
  const char* utf8 = PyUnicode_AsUTF8(s.get());
  if (utf8 == nullptr) {
    PyErr_Clear();
    return "<unprintable>";
  }
  return utf8;
}

// Convert the pending Python exception into cm::Error. GIL must be held.
[[noreturn]] void throw_python_error(const char* context) {
  PyObject *type = nullptr, *value = nullptr, *tb = nullptr;
  PyErr_Fetch(&type, &value, &tb);
  PyErr_NormalizeException(&type, &value, &tb);
  PyRef t(type), v(value), b(tb);
  std::string name = "UnknownError";
  if (type != nullptr) {
    PyRef n(PyObject_GetAttrString(type, "__name__"));
    if (n) {
      name = to_string(n.get());
    } else {
      PyErr_Clear();
    }
  }
  std::string message = to_string(value);
  // Keep the chained cause: RegisterAccessError wraps the MCU's "e ..." reply.
  if (value != nullptr) {
    PyRef cause(PyObject_GetAttrString(value, "__cause__"));
    if (cause && cause.get() != Py_None) {
      message += " (caused by: " + to_string(cause.get()) + ")";
    }
    PyErr_Clear();
  }
  throw Error(name, std::string(context) + ": " + message);
}

void ensure_interpreter() {
  static std::once_flag once;
  std::call_once(once, [] {
    if (Py_IsInitialized()) return;  // host program owns the interpreter
    Py_InitializeEx(0);              // 0: leave the host's signal handlers alone
#if PY_VERSION_HEX < 0x03070000
    PyEval_InitThreads();  // 3.6 creates the GIL lazily
#endif
    // Drop the GIL so any thread, including this one, can take it via Gil.
    PyEval_SaveThread();
  });
}

}  // namespace

Interface::Interface(const std::string& setup, const std::string& dev_path)
    : registry_(nullptr) {
  ensure_interpreter();
  Gil gil;
  PyRef module(PyImport_ImportModule("cm_interface.registry"));
  if (!module) throw_python_error("import cm_interface.registry");
  PyRef cls(PyObject_GetAttrString(module.get(), "Registry"));
  if (!cls) throw_python_error("cm_interface.registry.Registry");

  PyRef args(PyTuple_New(0));
  PyRef kwargs(Py_BuildValue("{s:s,s:s}", "setup", setup.c_str(), "dev_path",
                             dev_path.c_str()));
  if (!args || !kwargs) throw_python_error("Registry arguments");
  PyRef registry(PyObject_Call(cls.get(), args.get(), kwargs.get()));
  if (!registry) throw_python_error("Registry()");
  registry_ = registry.release();
}

Interface::~Interface() {
  if (registry_ != nullptr && Py_IsInitialized()) {
    Gil gil;
    Py_DECREF(static_cast<PyObject*>(registry_));
  }
}

std::uint8_t Interface::firefly_status(const std::string& location) {
  Gil gil;
  PyObject* registry = static_cast<PyObject*>(registry_);
  PyRef ff(PyObject_CallMethod(registry, "get_firefly", "s", location.c_str()));
  if (!ff) throw_python_error(("get_firefly(" + location + ")").c_str());
  PyRef status(PyObject_GetAttrString(ff.get(), "status"));
  if (!status) throw_python_error((location + ".status").c_str());
  long value = PyLong_AsLong(status.get());
  if (value == -1 && PyErr_Occurred()) {
    throw_python_error((location + ".status").c_str());
  }
  if (value < 0 || value > 0xFF) {
    throw Error("ValueError", location + ".status out of byte range: " +
                                  std::to_string(value));
  }
  return static_cast<std::uint8_t>(value);
}

}  // namespace cm
