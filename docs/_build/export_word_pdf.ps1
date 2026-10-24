param([Parameter(Mandatory=$true)][string]$InputDocx, [Parameter(Mandatory=$true)][string]$OutputPdf)
$ErrorActionPreference = 'Stop'
$wordApp = $null
$wordDoc = $null
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3
    $wordDoc = $wordApp.Documents.Open($InputDocx, $false, $false)
    $wordDoc.Fields.Update() | Out-Null
    foreach ($wordToc in $wordDoc.TablesOfContents) { $wordToc.Update() }
    $wordDoc.Repaginate()
    foreach ($wordToc in $wordDoc.TablesOfContents) { $wordToc.UpdatePageNumbers() }
    $wordDoc.Save()
    $wordDoc.ExportAsFixedFormat($OutputPdf, 17)
    Write-Output ('Pages: ' + $wordDoc.ComputeStatistics(2))
    Write-Output ('PDF: ' + $OutputPdf)
} finally {
    if ($null -ne $wordDoc) { $wordDoc.Close(0); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordDoc) }
    if ($null -ne $wordApp) { $wordApp.Quit(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApp) }
}
