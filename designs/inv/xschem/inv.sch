v {xschem version=3.4.8RC file_version=1.2
}
G {}
K {type=subcircuit
format="@name @pinlist @symname"
template="name=x1"
}
V {}
S {}
E {}
N -20 -80 -20 0 {lab=in}
N -20 0 -20 80 {lab=in}
N 20 -50 20 0 {lab=out}
N 20 0 20 50 {lab=out}
N 20 0 60 0 {lab=out}
N -40 0 -20 0 {lab=in}
N 20 -140 20 -110 {lab=vdd}
N 20 -140 50 -140 {lab=vdd}
N 50 -140 50 -80 {lab=vdd}
N 20 -80 50 -80 {lab=vdd}
N 20 110 20 140 {lab=vss}
N 20 140 50 140 {lab=vss}
N 50 80 50 140 {lab=vss}
N 20 80 50 80 {lab=vss}
C {sky130_fd_pr/pfet_01v8.sym} 0 -80 0 0 {name=M1
L=0.15
W=2
nf=1
mult=1
model=pfet_01v8
spiceprefix=X
}
C {sky130_fd_pr/nfet_01v8.sym} 0 80 0 0 {name=M2
L=0.15
W=1
nf=1
mult=1
model=nfet_01v8
spiceprefix=X
}
C {devices/ipin.sym} -40 0 0 0 {name=p1 lab=in}
C {devices/opin.sym} 60 0 0 0 {name=p2 lab=out}
C {devices/iopin.sym} 20 -140 0 0 {name=p3 lab=vdd}
C {devices/iopin.sym} 20 140 0 0 {name=p4 lab=vss}
