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
Requires:       python3 >= 3.6
Requires:       python3-pyserial

%description
Python library for UART management of command-module clocks, Firefly modules,
power supplies, MCU registers, and raw FPGA generic interfaces.

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
install -m 0644 device/*.py %{buildroot}%{python3_sitelib}/cm_interface/device/
install -m 0644 registers/*.json %{buildroot}%{python3_sitelib}/cm_interface/registers/
install -m 0644 tools/*.py %{buildroot}%{python3_sitelib}/cm_interface/tools/
# Each tool module gets a cm-<name> launcher (underscores become dashes).
for tool in clock_checks clock_dump exercise_mcu ff_status lga80d_checks \
            lga80d_dump lga80d_status mcu_dump mcu_set_rtc; do
    launcher=%{buildroot}%{_bindir}/cm-$(echo "$tool" | tr _ -)
    printf '#!%{__python3}\nimport sys\nfrom cm_interface.tools.%s import main\nsys.exit(main())\n' \
        "$tool" > "$launcher"
    chmod 0755 "$launcher"
done

%check
PYTHONPATH=%{buildroot}%{python3_sitelib} %{__python3} -m pytest tests/ --ignore=tests/hw

%files
%doc README.md EXAMPLES.md MCU_REGISTER_MAP.md
%{python3_sitelib}/cm_interface/
%{_bindir}/cm-*
