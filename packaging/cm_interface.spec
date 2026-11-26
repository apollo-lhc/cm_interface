%{!?package_version:%global package_version 0.0.0}
%{!?package_release:%global package_release 1}

Name:           python3-cm-interface
Version:        %{package_version}
Release:        %{package_release}%{?dist}
Summary:        UART management of command-module hardware
License:        MIT
Source0:        cm_interface-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-pytest
BuildRequires:  python3-pyserial
# Only for building the C++ sources in %check; nothing compiled is packaged.
BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  make
Requires:       python3 >= 3.6
Requires:       python3-pyserial

%description
Python library for UART management of command-module clocks, Firefly modules,
power supplies, MCU registers, and raw FPGA generic interfaces.

%package -n cm-interface-cpp-devel
Summary:        C++ sources for embedding the cm_interface Python package
Requires:       python3-cm-interface = %{version}-%{release}
Requires:       python3-devel
Requires:       cmake
Requires:       gcc-c++
Requires:       make

%description -n cm-interface-cpp-devel
CMake project, header and sources of the C++ layer that embeds the system
Python and calls cm_interface, installed under %{_datadir}/cm_interface/cpp
as a starting point for further C++ bindings. Copy the tree and build it with
cmake; nothing is precompiled.

%prep
%setup -q -n cm_interface-%{version}

%build
# Pure Python; no compilation step is required.

%install
install -d %{buildroot}%{python3_sitelib}/cm_interface/device
install -d %{buildroot}%{python3_sitelib}/cm_interface/registers
install -d %{buildroot}%{python3_sitelib}/cm_interface/tools
install -d %{buildroot}%{_bindir}
install -m 0644 *.py %{buildroot}%{python3_sitelib}/cm_interface/
# Stamp the RPM version into the package (cm_interface.__version__).
printf '__version__ = "%s"\n' '%{version}' \
    > %{buildroot}%{python3_sitelib}/cm_interface/_version.py
install -m 0644 device/*.py %{buildroot}%{python3_sitelib}/cm_interface/device/
install -m 0644 registers/*.json %{buildroot}%{python3_sitelib}/cm_interface/registers/
install -m 0644 tools/*.py %{buildroot}%{python3_sitelib}/cm_interface/tools/
# vccint_2phase rewrites LGA80D rail configuration; keep it out of the RPM.
rm %{buildroot}%{python3_sitelib}/cm_interface/tools/vccint_2phase.py
# Each tool module gets a cm-<name> launcher (underscores become dashes).
for tool in clock_checks clock_dump ff_cdr ff_dump ff_rx_eq ff_status \
            is_cm_healthy is_cm_power_on is_fpga_programmed is_rtc_set \
            lga80d_checks lga80d_clear_faults lga80d_dump lga80d_snapshot_reset lga80d_status \
            mcu_control mcu_dump mcu_set_rtc wait_cm_power_on; do
    launcher=%{buildroot}%{_bindir}/cm-$(echo "$tool" | tr _ -)
    printf '#!%{__python3}\nimport sys\nfrom cm_interface.tools.%s import main\nsys.exit(main())\n' \
        "$tool" > "$launcher"
    chmod 0755 "$launcher"
done
install -d %{buildroot}%{_datadir}/cm_interface
cp -a cpp %{buildroot}%{_datadir}/cm_interface/

%check
PYTHONPATH=%{buildroot}%{python3_sitelib} %{__python3} -m pytest tests/ --ignore=tests/hw
# The packaged C++ tree must build as shipped (it is not run here: rpm.yml
# smoke-tests it after installing both packages).
cmake -S %{buildroot}%{_datadir}/cm_interface/cpp -B cpp-check-build \
    -DPython3_EXECUTABLE=%{__python3} -DCMAKE_CXX_FLAGS=-Werror
cmake --build cpp-check-build

%files
%doc README.md EXAMPLES.md MCU_REGISTER_MAP.md
%{python3_sitelib}/cm_interface/
%{_bindir}/cm-*

%files -n cm-interface-cpp-devel
%{_datadir}/cm_interface/
