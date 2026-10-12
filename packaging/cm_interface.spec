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
install -m 0644 *.py %{buildroot}%{python3_sitelib}/cm_interface/
install -m 0644 device/*.py %{buildroot}%{python3_sitelib}/cm_interface/device/
install -m 0644 registers/*.json %{buildroot}%{python3_sitelib}/cm_interface/registers/

%check
PYTHONPATH=%{buildroot}%{python3_sitelib} %{__python3} -m pytest tests/ --ignore=tests/hw

%files
%doc README.md EXAMPLES.md MCU_REGISTER_MAP.md
%{python3_sitelib}/cm_interface/
