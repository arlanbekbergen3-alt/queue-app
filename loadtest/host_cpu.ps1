param([int]$Samples = 60, [int]$Interval = 2)
"ts,host_cpu_percent"
for ($i = 0; $i -lt $Samples; $i++) {
    $row = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
    $stamp = [int][double](Get-Date -UFormat %s)
    "{0},{1}" -f $stamp, $row.PercentProcessorTime
    Start-Sleep -Seconds $Interval
}
