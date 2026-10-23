# ----------------------------------------------
# Script Recorded by Ansys Electronics Desktop Version 2026.1.2
# 20:04:04  Aug 12, 2026
# ----------------------------------------------
import ScriptEnv
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
oDesktop.RestoreWindow()
oProject = oDesktop.SetActiveProject("SMA_Coupon_GCPW_Opt")
oDesign = oProject.SetActiveDesign("SMA_Coupon_GCPW_Opt_4L")
oEditor = oDesign.SetActiveEditor("3D Modeler")
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "Y",
		"RotateAngle:="		, "90deg"
	])
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "X",
		"RotateAngle:="		, "90deg"
	])
oProject.Save()
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "PML_Sheet_Right",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-30mm",
		"TranslateVectorY:="	, "2.66453525910038e-15mm",
		"TranslateVectorZ:="	, "-0.0175000000000005mm"
	])
oProject.Save()
