cmake_minimum_required(VERSION 3.12)  # FindPython3
project(cm_interface_cpp CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# On EL8 this finds the system 3.6 (python36-devel). Point it elsewhere with
# -DPython3_EXECUTABLE=/path/to/python3.
find_package(Python3 REQUIRED COMPONENTS Interpreter Development)

add_library(cm_interface_cpp src/cm_interface.cpp)
target_include_directories(cm_interface_cpp PUBLIC include)
target_link_libraries(cm_interface_cpp PRIVATE Python3::Python)
target_compile_options(cm_interface_cpp PRIVATE -Wall -Wextra)
set_target_properties(cm_interface_cpp PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_executable(ff_status_byte examples/ff_status_byte.cpp)
target_link_libraries(ff_status_byte PRIVATE cm_interface_cpp)
